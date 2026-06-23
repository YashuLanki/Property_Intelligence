# Vaulter AI Property Intelligence System

An end-to-end AI system built for a real estate investment company to automate
market intelligence, document analysis, and broker email processing.

Built as a data analyst intern project using Python, Claude AI, and a modern
RAG (Retrieval-Augmented Generation) architecture — accessible directly through
the team's existing Claude.ai Team subscription via an MCP server.

---

## System Overview

| Stage | Name | Description | Status |
|-------|------|-------------|--------|
| 1 | PDF Ingestion | Watches a folder, extracts text from PDFs (including scanned documents via OCR), and stores chunks in a vector database | ✅ Complete |
| 2 | Web & Email Pipeline | Scrapes public market data, pulls broker emails, and searches for property-specific intelligence tied to the Vaulter Project Master | ✅ Complete |
| 3 | MCP Server | Exposes the full database as tools Claude.ai can call — no separate UI needed, team uses claude.ai directly | ✅ Complete |
| 4 | Speech-to-Knowledge | Records Monday meetings, transcribes audio, and extracts structured property updates | 🔜 Planned |

---

## How the Team Uses It

1. Open **claude.ai** (already on Team plan — no extra cost)
2. Go to **Settings → Connectors** and connect **Vaulter AI Property Intelligence**
3. Ask questions in plain English — Claude automatically calls the right tools:
   - *"What's the latest on Mesa Del Sol?"*
   - *"Any new broker emails this week?"*
   - *"Run a risk scan on our Arizona portfolio"*
   - *"List all properties in Final Engineering"*

No separate app, no browser tab, no login — just Claude.ai the team already uses.

---

## Tech Stack

- **PDF Extraction** — pdfplumber, Tesseract OCR, pdf2image
- **Vector Database** — ChromaDB
- **AI Analysis** — Anthropic Claude API
- **Web Scraping** — BeautifulSoup, Requests
- **Email Integration** — Microsoft Graph API (Outlook), MSAL
- **OCR** — Tesseract (PDFs, image-based Project Master, image email attachments)
- **Document Parsing** — mammoth (Word), openpyxl (Excel), python-pptx (PowerPoint)
- **Scheduling** — APScheduler
- **MCP Server** — FastMCP (connects database to claude.ai)
- **Transcription** — OpenAI Whisper (Stage 4)

---

## Project Structure

```
vaulter-ai/
├── main.py                    # Entry point — all commands run from here
├── config.py                  # All settings and paths in one place
├── requirements.txt           # All dependencies
├── README.md                  # This file
│
├── ingestion/                 # Stage 1 — PDF Ingestion
│   ├── extractor.py           # PDF text extraction + OCR fallback
│   ├── chunker.py             # Splits text into overlapping chunks
│   ├── embedder.py            # ChromaDB vector storage and retrieval
│   ├── watcher.py             # Folder monitoring and ingestion pipeline
│   └── registry.py            # Duplicate detection via file hashing
│
├── pipeline/                  # Stage 2 — Web & Email Data Pipeline
│   ├── web_scraper.py         # Public web scraping (reads from sources.csv)
│   ├── property_scraper.py    # Property-specific news & market data for all properties
│   ├── property_matcher.py    # Matches web/email content to Project Master properties
│   ├── email_reader.py        # Outlook email reader — handles all attachment types
│   ├── outlook_auth.py        # Microsoft OAuth2 authentication
│   └── scheduler.py           # Background scheduler for all automated jobs
│
├── analysis/                  # Stage 3 — RAG Engine
│   ├── __init__.py
│   ├── rag_engine.py          # ChromaDB retrieval and context assembly
│   ├── analyzer.py            # Claude API calls — summaries, risk flags, Q&A
│   └── prompts.py             # All Claude prompts in one place
│
├── mcp_server.py              # Stage 3 — MCP server (connects everything to claude.ai)
│
├── speech/                    # Stage 4 — Speech-to-Knowledge (planned)
│   └── __init__.py
│
├── confidentials/             # Secrets — never committed to git
│   ├── .env                   # All API keys and credentials
│   └── outlook_token.json     # Auto-generated after Outlook auth
│
└── data/
    ├── watched_folder/        # Drop PDFs here — State/Property/file.pdf
    │   ├── Arizona/
    │   ├── California/
    │   ├── Colorado/
    │   ├── New Mexico/
    │   └── Texas/
    ├── processed/             # PDFs move here after ingestion
    ├── chroma_db/             # Vector database (all stages write here)
    ├── logs/                  # System logs
    ├── raw_web/               # Raw scraped text (audit trail)
    ├── raw_email/             # Raw email/attachment dumps (audit trail)
    ├── project_master/        # Drop Vaulter Project Master export here
    └── web_sources/
        └── sources.csv        # Add/remove web scraping sources here
```

---

## Setup

### 1. Clone the repository
```bash
git clone https://github.com/YashuLanki/vaulter-ai.git
cd vaulter-ai
```

### 2. Create a virtual environment
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
```

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Install external tools

**Windows:**
- **Tesseract OCR**: https://github.com/UB-Mannheim/tesseract/wiki
- **Poppler**: https://github.com/oschwartz10612/poppler-windows/releases

**Mac:**
```bash
brew install tesseract poppler
```

### 5. Update paths in config.py (Windows only)
```python
TESSERACT_PATH = r"C:\Users\YourName\Packages\Tesseract-OCR\tesseract.exe"
POPPLER_PATH   = r"C:\Users\YourName\Packages\poppler\Library\bin"
```

### 6. Set up credentials

Create `confidentials/.env`:
```
OUTLOOK_CLIENT_ID=your-application-id
OUTLOOK_TENANT_ID=your-directory-id
OUTLOOK_CLIENT_SECRET=your-client-secret
ANTHROPIC_API_KEY=sk-ant-your-key-here
MCP_API_KEY=your-random-secret-key-here
```

Generate a secure MCP key:
```bash
python -c "import secrets; print(secrets.token_hex(24))"
```

To get Outlook credentials:
1. Go to portal.azure.com → App registrations → New registration
2. Name it "Vaulter Email Pipeline" → Single tenant → Register
3. API Permissions → Microsoft Graph → Delegated → Mail.Read
4. Authentication → Mobile/desktop → tick http://localhost → Allow public client flows: Yes
5. Certificates & secrets → New client secret → copy the Value
6. Copy Application ID and Directory ID from Overview

### 7. Authorize Outlook (run once)
```bash
python main.py auth
```

### 8. Drop the Project Master into place
Export the Vaulter Project Master from Smartsheet (PDF, CSV, or Excel) and drop it
into `data/project_master/`.

### 9. Connect to claude.ai
1. Start the MCP server: `python main.py mcp`
2. Expose it via ngrok: `ngrok http 8765`
3. In claude.ai → Settings → Connectors → Add custom connector
4. Enter the ngrok URL and your MCP_API_KEY
5. Name it: **Vaulter AI Property Intelligence**

---

## Usage

### Stage 1 — PDF Ingestion
```bash
python main.py ingest                              # start the PDF watcher
python main.py stats                               # show full database statistics
python main.py query "flood zone Magic Ranch"      # search documents
```

Drop PDFs into `data/watched_folder/State/Property/` — ingestion is automatic.

### Stage 2 — Web & Email Pipeline
```bash
python main.py scrape                              # scrape all sources
python main.py email                               # pull new emails
python main.py email --days 30                     # pull last 30 days
python main.py property-scrape                     # scrape all 48 properties
python main.py properties                          # list all properties
python main.py schedule                            # run everything automatically
python main.py auth                                # authorize Outlook (once)
```

### Stage 3 — MCP Server
```bash
python main.py mcp                                 # start on default port 8765
python main.py mcp 9000                            # start on custom port
```

---

## Security Notes

- The MCP server requires `MCP_API_KEY` to be set — Claude.ai sends this key with every request
- Use ngrok to expose the server; never open the port directly on your router
- The `confidentials/` folder is gitignored — never commit it
- Anthropic's Team plan does not train on your content by default

---

*Built by Yashu Lanki — Data Analyst Intern, Vaulter*
