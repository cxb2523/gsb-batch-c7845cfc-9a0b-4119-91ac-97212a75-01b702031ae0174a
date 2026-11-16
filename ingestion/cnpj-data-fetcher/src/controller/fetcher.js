import path from 'path';
import download from '../service/download.js';
import extract from '../service/extract.js';
import scraping from '../service/scraping.js';
import storage from '../service/storage.js';
import alert from '../util/alert.js';
import logger from '../util/logger.js';

const TEMP_DIR = '/tmp/'; // Diretório temporário padrão do Google Cloud Run
const EXTRACTED_DIR = '/tmp/extracted/';

/**
 * Status possíveis para o processamento de um arquivo.
 */
const STATUS = {
    SUCCESS: 'success',
    SKIPPED: 'skipped',
    FAILED: 'failed'
};

/**
 * Erro lançado quando ao menos um arquivo da lista falha, mas o restante
 * do lote foi processado até o fim. Carrega o resumo completo da execução.
 */
class PartialFailureError extends Error {
    constructor(summary) {
        const failedNames = summary.failed.map((item) => item.fileName).join(', ');
        super(
            `Job finalizado com ${summary.failed.length} arquivo(s) com falha no diretório ${summary.period}: ${failedNames}`
        );
        this.name = 'PartialFailureError';
        this.summary = summary;
    }
}

/**
 * Envia um alerta sem permitir que falhas de notificação interrompam o
 * processamento dos arquivos. O mecanismo de envio (Slack via `alert.send`)
 * permanece o mesmo; aqui apenas garantimos o isolamento das falhas.
 *
 * @param {Object} deps - Dependências injetadas.
 * @param {string} message - Mensagem a ser enviada.
 * @returns {Promise<void>}
 */
const _notify = async (deps, message) => {
    try {
        await deps.alert.send(message);
    } catch (e) {
        logger.error(`Falha ao enviar alerta: ${e.message}`, 'fetcher.js:_notify');
    }
};

/**
 * 🔍 Obtém o diretório mais recente e a lista de arquivos ZIP disponíveis para download.
 *
 * @param {Object} deps - Dependências injetadas.
 * @returns {Promise<{ period: string, files: string[] }>} - Retorna o período e os arquivos ZIP disponíveis.
 *
 * @throws {Error} Se houver erro ao obter os dados via scraping.
 */
const _scrapeData = async (deps) => {
    try {
        await _notify(deps, '🔍 Buscando últimos arquivos disponíveis no site da Receita Federal...');

        const period = await deps.scraping.getLatestDirectory();
        if (!period) {
            throw new Error('Não foi possível obter os arquivos mais recentes.');
        }

        const files = await deps.scraping.getLatestFiles(period);
        if (!files.length) {
            throw new Error(`Nenhum arquivo encontrado no diretório ${period}.`);
        }

        await _notify(deps, `📂 Diretório mais atual: ${period}\n📄 Arquivos mais atuais: ${files.join(', ')}`);

        return { period, files };
    } catch (e) {
        await _notify(deps, `❌ Erro ao buscar arquivos no site da Receita Federal: ${e.message}`);
        throw e;
    }
};

/**
 * Executa o pipeline completo de download, extração e upload para um único arquivo.
 *
 * Esta função NÃO lança erros: qualquer falha de download, extração ou upload é
 * capturada, reportada via alerta e devolvida no resultado para que o lote
 * continue sendo processado. Arquivos já existentes no Storage continuam sendo
 * pulados sem novo download, na mesma lógica de antes.
 *
 * @param {string} period - Período da base de dados.
 * @param {string} fileName - Nome do arquivo ZIP a ser processado.
 * @param {Object} deps - Dependências injetadas.
 * @returns {Promise<{ fileName: string, status: string, error?: string }>}
 *          Resultado do processamento, com status `success`, `skipped` ou `failed`.
 */
const _processFile = async (period, fileName, deps) => {
    const zipPath = path.join(TEMP_DIR, fileName);
    const extractedFolder = EXTRACTED_DIR;
    const extractedFileName = fileName.replace('.zip', '.csv');

    try {
        const alreadyExists = await deps.storage.fileExists(period, extractedFileName);
        if (alreadyExists) {
            await _notify(deps, `✅ O arquivo ${extractedFileName} não precisa ser processado pois já está no Storage.`);
            return { fileName, status: STATUS.SKIPPED };
        }

        await _notify(deps, `📥 Baixando arquivo: ${fileName}...`);
        await deps.download.fileFromUrl(period, fileName, zipPath);

        await _notify(deps, `🪤 Extraindo arquivo: ${fileName}...`);
        const extractedFilePath = await deps.extract.file(fileName, zipPath, extractedFolder);

        await _notify(deps, `⏳ Fazendo upload: ${extractedFileName}...`);
        await deps.storage.uploadFile(period, extractedFileName, extractedFilePath);

        await _notify(deps, `✅ Arquivo armazenado no Storage: ${fileName}`);
        return { fileName, status: STATUS.SUCCESS };
    } catch (e) {
        await _notify(deps, `❌ Erro ao manipular o arquivo ${fileName}: ${e.message}`);
        return { fileName, status: STATUS.FAILED, error: e.message };
    }
};

/**
 * Consolida os resultados individuais dos arquivos em um resumo por status.
 *
 * @param {string} period - Período da base de dados.
 * @param {Array<{ fileName: string, status: string, error?: string }>} results
 * @returns {{ period: string, total: number, succeeded: Array, skipped: Array, failed: Array }}
 */
const _buildSummary = (period, results) => ({
    period,
    total: results.length,
    succeeded: results.filter((result) => result.status === STATUS.SUCCESS),
    skipped: results.filter((result) => result.status === STATUS.SKIPPED),
    failed: results.filter((result) => result.status === STATUS.FAILED)
});

/**
 * Formata uma seção do resumo, omitindo seções vazias.
 *
 * @param {string} title - Título da seção.
 * @param {string[]} lines - Linhas de detalhe da seção.
 * @returns {string} Seção formatada ou string vazia.
 */
const _formatSection = (title, lines) => {
    if (!lines.length) return '';
    return `${title}:\n${lines.map((line) => `  • ${line}`).join('\n')}`;
};

/**
 * Monta a mensagem final de resumo, separando quantidades e detalhes de
 * sucessos, arquivos ignorados e falhas.
 *
 * @param {{ period: string, total: number, succeeded: Array, skipped: Array, failed: Array }} summary
 * @returns {string} Mensagem de resumo.
 */
const _buildSummaryMessage = (summary) => {
    const { period, total, succeeded, skipped, failed } = summary;
    const lines = [
        `Resumo do diretório ${period} — total: ${total} | ✅ sucesso: ${succeeded.length} | ` +
            `⏭️ ignorados: ${skipped.length} | ❌ falhas: ${failed.length}`
    ];

    const successSection = _formatSection(
        '✅ Sucesso',
        succeeded.map((result) => result.fileName)
    );
    if (successSection) lines.push(successSection);

    const skippedSection = _formatSection(
        '⏭️ Ignorados (já existem no Storage)',
        skipped.map((result) => result.fileName)
    );
    if (skippedSection) lines.push(skippedSection);

    const failedSection = _formatSection(
        '❌ Falhas',
        failed.map((result) => `${result.fileName} — ${result.error}`)
    );
    if (failedSection) lines.push(failedSection);

    return lines.join('\n');
};

/**
 * Inicia o pipeline de processamento, executando os arquivos um por um (SEM PARALELISMO).
 *
 * Cada arquivo é tratado de forma independente: uma falha de download, extração
 * ou upload é registrada com o nome do arquivo e o motivo, e o processamento
 * continua para os próximos arquivos. Ao final:
 *
 * - Se todos os arquivos forem processados com sucesso ou estiverem pulados
 *   (já existentes no Storage), o diretório é marcado como processado e o job
 *   encerra com sucesso.
 * - Se houver ao menos uma falha, o diretório NÃO é marcado como processado e
 *   o job encerra lançando `PartialFailureError`, com a lista de falhas
 *   disponível no erro e na mensagem de resumo enviada ao Slack.
 *
 * @param {Object} [injectedDeps={}] - Dependências injetáveis (usado em testes).
 * @returns {Promise<void>}
 *
 * @throws {PartialFailureError} Quando um ou mais arquivos falham.
 * @throws {Error} Para falhas anteriores ao loop de arquivos (scraping etc.).
 */
const run = async (injectedDeps = {}) => {
    const deps = {
        scraping,
        storage,
        download,
        extract,
        alert,
        ...injectedDeps
    };

    try {
        await _notify(deps, '🏂 O job foi iniciado.');
        const { period, files } = await _scrapeData(deps);

        if (await deps.storage.wasFolderProcessed(period)) {
            await _notify(deps, `🫡 O job encerrado pois o diretório ${period} já foi processado.`);
            return;
        }

        const results = [];
        for (const file of files) {
            const result = await _processFile(period, file, deps);
            results.push(result);
        }

        const summary = _buildSummary(period, results);

        if (summary.failed.length) {
            await _notify(
                deps,
                `❌ O job foi encerrado com falha: ${summary.failed.length} de ${summary.total} ` +
                    `arquivo(s) apresentaram erro.\n${_buildSummaryMessage(summary)}`
            );
            throw new PartialFailureError(summary);
        }

        await deps.storage.updateLastProcessedFolder(period);

        await _notify(deps, `🫡 O job foi encerrado com sucesso.\n${_buildSummaryMessage(summary)}`);
    } catch (e) {
        if (e instanceof PartialFailureError) {
            throw e;
        }
        await _notify(deps, `❌ O job foi encerrado com falha: ${e.message}`);
        throw e;
    }
};

export default { run };
export { PartialFailureError, STATUS };
