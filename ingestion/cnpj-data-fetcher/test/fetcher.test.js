import { test } from 'node:test';
import assert from 'node:assert/strict';

// Os módulos reais de serviço instanciam clientes do GCP no carregamento
// (ex.: `storage.bucket(env.bucket)`). As dependências usadas pelos testes
// são todas falsas, mas estas variáveis permitem que os módulos sejam
// importados de forma hermética, sem credenciais nem `.env`.
process.env.GOOGLE_CLOUD_PROJECT = 'test-project';
process.env.GOOGLE_CLOUD_BUCKET = 'test-bucket';
process.env.LOG_ENABLED = 'true';

const { default: fetcher, PartialFailureError, STATUS } = await import(
    '../src/controller/fetcher.js'
);

const PERIOD = '2026-01';
const FILES = ['A.zip', 'B.zip', 'C.zip'];

/**
 * Constrói dependências falsas para o controller, registrando todas as
 * chamadas realizadas (downloads, extrações, uploads, marcações e alertas).
 * Qualquer dependência pode ser sobrescrita para simular falhas parciais.
 *
 * @param {Object} [overrides={}] - Substituições de dependências.
 * @returns {Object} Dependências + objeto `calls` com o histórico.
 */
const createDeps = (overrides = {}) => {
    const calls = {
        wasFolderProcessed: [],
        fileExists: [],
        downloads: [],
        extracts: [],
        uploads: [],
        updateLastProcessedFolder: [],
        alerts: []
    };

    const deps = {
        scraping: {
            getLatestDirectory: async () => PERIOD,
            getLatestFiles: async () => [...FILES]
        },
        storage: {
            wasFolderProcessed: async (folder) => {
                calls.wasFolderProcessed.push(folder);
                return false;
            },
            fileExists: async (period, fileName) => {
                calls.fileExists.push({ period, fileName });
                return false;
            },
            uploadFile: async (period, fileName, filePath) => {
                calls.uploads.push({ period, fileName, filePath });
            },
            updateLastProcessedFolder: async (folder) => {
                calls.updateLastProcessedFolder.push(folder);
            }
        },
        download: {
            fileFromUrl: async (period, fileName, destination) => {
                calls.downloads.push({ period, fileName, destination });
                return destination;
            }
        },
        extract: {
            file: async (fileName, zipPath, extractedFolder) => {
                calls.extracts.push({ fileName, zipPath, extractedFolder });
                return `${extractedFolder}${fileName.replace('.zip', '.csv')}`;
            }
        },
        alert: {
            send: async (message) => {
                calls.alerts.push(message);
            }
        },
        calls
    };

    Object.assign(deps, overrides);
    return deps;
};

test('STATUS expõe os três resultados possíveis', () => {
    assert.deepEqual(STATUS, { SUCCESS: 'success', SKIPPED: 'skipped', FAILED: 'failed' });
});

test('falha de download em um arquivo não interrompe o lote e marca o job como falho', async () => {
    const deps = createDeps({
        download: {
            fileFromUrl: async (period, fileName, destination) => {
                deps.calls.downloads.push({ period, fileName, destination });
                if (fileName === 'B.zip') {
                    throw new Error('falha simulada de download');
                }
                return destination;
            }
        }
    });

    let caught;
    try {
        await fetcher.run(deps);
    } catch (e) {
        caught = e;
    }

    // O job termina com falha e carrega a lista de falhas no erro.
    assert.ok(caught instanceof PartialFailureError);
    assert.equal(caught.summary.period, PERIOD);
    assert.equal(caught.summary.failed.length, 1);
    assert.deepEqual(caught.summary.failed[0], {
        fileName: 'B.zip',
        status: 'failed',
        error: 'falha simulada de download'
    });
    assert.equal(caught.summary.succeeded.length, 2);
    assert.equal(caught.summary.skipped.length, 0);

    // Todos os arquivos foram tentados, inclusive os posteriores à falha.
    assert.deepEqual(
        deps.calls.downloads.map((item) => item.fileName),
        ['A.zip', 'B.zip', 'C.zip']
    );

    // Os arquivos que não falharam seguiram o fluxo completo de extração e upload.
    assert.deepEqual(
        deps.calls.uploads.map((item) => item.fileName),
        ['A.csv', 'C.csv']
    );

    // Regras de caminho e nomenclatura preservadas: .zip no download, .csv no upload.
    assert.match(deps.calls.downloads[0].destination, /A\.zip$/);
    assert.match(deps.calls.uploads[0].filePath, /A\.csv$/);

    // Com falhas, o diretório NUNCA é marcado como processado.
    assert.deepEqual(deps.calls.updateLastProcessedFolder, []);

    // Alertas individuais e resumo final trazem nome do arquivo e motivo do erro.
    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /Erro ao manipular o arquivo B\.zip: falha simulada de download/);
    assert.match(joined, /sucesso: 2/);
    assert.match(joined, /ignorados: 0/);
    assert.match(joined, /falhas: 1/);
    assert.match(joined, /B\.zip — falha simulada de download/);
    assert.match(joined, /A\.zip/);
    assert.match(joined, /C\.zip/);
});

test('arquivos já existentes no Storage são pulados sem download e o diretório é marcado', async () => {
    const deps = createDeps({
        storage: {
            wasFolderProcessed: async (folder) => {
                deps.calls.wasFolderProcessed.push(folder);
                return false;
            },
            fileExists: async (period, fileName) => {
                deps.calls.fileExists.push({ period, fileName });
                return fileName === 'B.csv';
            },
            uploadFile: async (period, fileName, filePath) => {
                deps.calls.uploads.push({ period, fileName, filePath });
            },
            updateLastProcessedFolder: async (folder) => {
                deps.calls.updateLastProcessedFolder.push(folder);
            }
        }
    });

    // Sem falhas: a Promise resolve normalmente.
    await assert.doesNotReject(fetcher.run(deps));

    // B.zip já existia e por isso não foi baixado nem extraído.
    assert.deepEqual(
        deps.calls.downloads.map((item) => item.fileName),
        ['A.zip', 'C.zip']
    );
    assert.ok(!deps.calls.extracts.some((item) => item.fileName === 'B.zip'));
    assert.deepEqual(
        deps.calls.uploads.map((item) => item.fileName),
        ['A.csv', 'C.csv']
    );

    // Todos os arquivos bem-sucedidos ou pulados: diretório marcado.
    assert.deepEqual(deps.calls.updateLastProcessedFolder, [PERIOD]);

    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /já está no Storage/);
    assert.match(joined, /sucesso: 2/);
    assert.match(joined, /ignorados: 1/);
    assert.match(joined, /falhas: 0/);
});

test('todos os arquivos pulados: nenhum download e diretório ainda assim marcado', async () => {
    const deps = createDeps({
        storage: {
            wasFolderProcessed: async (folder) => {
                deps.calls.wasFolderProcessed.push(folder);
                return false;
            },
            fileExists: async (period, fileName) => {
                deps.calls.fileExists.push({ period, fileName });
                return true;
            },
            uploadFile: async (period, fileName, filePath) => {
                deps.calls.uploads.push({ period, fileName, filePath });
            },
            updateLastProcessedFolder: async (folder) => {
                deps.calls.updateLastProcessedFolder.push(folder);
            }
        }
    });

    await assert.doesNotReject(fetcher.run(deps));

    assert.deepEqual(deps.calls.downloads, []);
    assert.deepEqual(deps.calls.uploads, []);
    assert.deepEqual(deps.calls.updateLastProcessedFolder, [PERIOD]);

    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /sucesso: 0/);
    assert.match(joined, /ignorados: 3/);
    assert.match(joined, /falhas: 0/);
});

test('falha no upload de um arquivo isola o erro e impede a marcação do diretório', async () => {
    const deps = createDeps({
        storage: {
            wasFolderProcessed: async (folder) => {
                deps.calls.wasFolderProcessed.push(folder);
                return false;
            },
            fileExists: async (period, fileName) => {
                deps.calls.fileExists.push({ period, fileName });
                return false;
            },
            uploadFile: async (period, fileName, filePath) => {
                deps.calls.uploads.push({ period, fileName, filePath });
                if (fileName === 'A.csv') {
                    throw new Error('falha simulada de upload');
                }
            },
            updateLastProcessedFolder: async (folder) => {
                deps.calls.updateLastProcessedFolder.push(folder);
            }
        }
    });

    await assert.rejects(fetcher.run(deps), PartialFailureError);

    // Download e extração aconteceram para todos, mesmo após a falha do primeiro upload.
    assert.equal(deps.calls.downloads.length, 3);
    assert.equal(deps.calls.extracts.length, 3);
    assert.deepEqual(
        deps.calls.uploads.map((item) => item.fileName),
        ['A.csv', 'B.csv', 'C.csv']
    );
    assert.deepEqual(deps.calls.updateLastProcessedFolder, []);

    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /A\.zip — falha simulada de upload/);
    assert.match(joined, /falhas: 1/);
});

test('cenário misto distingue sucesso, pulado e falha no resumo final', async () => {
    const deps = createDeps({
        storage: {
            wasFolderProcessed: async (folder) => {
                deps.calls.wasFolderProcessed.push(folder);
                return false;
            },
            // B.zip já existe no Storage.
            fileExists: async (period, fileName) => {
                deps.calls.fileExists.push({ period, fileName });
                return fileName === 'B.csv';
            },
            uploadFile: async (period, fileName, filePath) => {
                deps.calls.uploads.push({ period, fileName, filePath });
            },
            updateLastProcessedFolder: async (folder) => {
                deps.calls.updateLastProcessedFolder.push(folder);
            }
        },
        download: {
            // C.zip falha no download; A.zip processa normalmente.
            fileFromUrl: async (period, fileName, destination) => {
                deps.calls.downloads.push({ period, fileName, destination });
                if (fileName === 'C.zip') {
                    throw new Error('timeout simulado');
                }
                return destination;
            }
        }
    });

    let caught;
    try {
        await fetcher.run(deps);
    } catch (e) {
        caught = e;
    }

    assert.ok(caught instanceof PartialFailureError);
    assert.deepEqual(
        caught.summary.succeeded.map((item) => item.fileName),
        ['A.zip']
    );
    assert.deepEqual(
        caught.summary.skipped.map((item) => item.fileName),
        ['B.zip']
    );
    assert.deepEqual(
        caught.summary.failed.map((item) => item.fileName),
        ['C.zip']
    );
    assert.deepEqual(deps.calls.updateLastProcessedFolder, []);

    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /total: 3/);
    assert.match(joined, /sucesso: 1/);
    assert.match(joined, /ignorados: 1/);
    assert.match(joined, /falhas: 1/);
    assert.match(joined, /C\.zip — timeout simulado/);
    assert.match(joined, /Ignorados \(já existem no Storage\)/);
});

test('diretório já processado encerra o job sem tocar nos arquivos', async () => {
    const deps = createDeps({
        storage: {
            wasFolderProcessed: async (folder) => {
                deps.calls.wasFolderProcessed.push(folder);
                return true;
            },
            fileExists: async (period, fileName) => {
                deps.calls.fileExists.push({ period, fileName });
                return false;
            },
            uploadFile: async (period, fileName, filePath) => {
                deps.calls.uploads.push({ period, fileName, filePath });
            },
            updateLastProcessedFolder: async (folder) => {
                deps.calls.updateLastProcessedFolder.push(folder);
            }
        }
    });

    await assert.doesNotReject(fetcher.run(deps));

    assert.deepEqual(deps.calls.fileExists, []);
    assert.deepEqual(deps.calls.downloads, []);
    assert.deepEqual(deps.calls.uploads, []);
    assert.deepEqual(deps.calls.updateLastProcessedFolder, []);
    assert.ok(deps.calls.alerts.some((message) => message.includes('já foi processado')));
});

test('falha no scraping continua sendo fatal e não marca o diretório', async () => {
    const scrapingError = new Error('WebDAV indisponível');
    const deps = createDeps({
        scraping: {
            getLatestDirectory: async () => {
                throw scrapingError;
            },
            getLatestFiles: async () => [...FILES]
        }
    });

    await assert.rejects(fetcher.run(deps), (error) => error === scrapingError);

    assert.deepEqual(deps.calls.downloads, []);
    assert.deepEqual(deps.calls.updateLastProcessedFolder, []);
    const joined = deps.calls.alerts.join('\n');
    assert.match(joined, /Erro ao buscar arquivos no site da Receita Federal: WebDAV indisponível/);
    assert.match(joined, /O job foi encerrado com falha: WebDAV indisponível/);
});

test('falha ao enviar alerta do Slack não interrompe o processamento dos arquivos', async () => {
    const deps = createDeps({
        alert: {
            send: async () => {
                throw new Error('webhook fora do ar');
            }
        }
    });

    await assert.doesNotReject(fetcher.run(deps));
    assert.equal(deps.calls.downloads.length, 3);
    assert.equal(deps.calls.uploads.length, 3);
    assert.deepEqual(deps.calls.updateLastProcessedFolder, [PERIOD]);
});
