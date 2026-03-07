# Profile Data Generation for VoiceRAG

This guide describes how to generate Q&A profile data from your VoiceRAG knowledge base for use in HuggingFace datasets and LLM fine-tuning.

## Overview

The `generate_qa_profile_data.py` script:

1. **Reads documents** from the `data/` folder (Markdown and PDF)
2. **Generates 100 questions** using Azure OpenAI based on document content
3. **Calls the VoiceRAG WebSocket API** (`/realtime`) for each question to get RAG-grounded answers and audio
4. **Creates audio files** for questions (local TTS) and answers (from realtime-mini WebSocket, converted from PCM to MP3)
5. **Outputs a JSONL file** in `profile_data/` with question-answer pairs

## Prerequisites

- Python 3.11+ with virtual environment
- VoiceRAG backend deployed with WebSocket endpoint (`/realtime`)
- Azure OpenAI credentials (for question generation and local TTS)
- Documents in the `data/` folder

## Setup

1. Install dependencies:
   ```bash
   # Install backend requirements (for imports) and script-specific requirements
   pip install -r app/backend/requirements.txt -r scripts/requirements.txt
   ```
   
   **Note:** The script uses `pydub` to convert PCM audio to MP3, which requires `ffmpeg` to be installed on your system:
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt-get install ffmpeg` or `sudo yum install ffmpeg`
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)

2. Configure environment (one of):
   - `app/backend/.env` with `AZURE_OPENAI_*` and `BACKEND_URI` or `VOICERAG_ENDPOINT`
   - `.azure/voicerag-aisearch-openai-rag-audio/.env` (from `azd up`)

## Usage

### Using the shell script

```bash
# Uses BACKEND_URI from .azure/voicerag-aisearch-openai-rag-audio/.env if present
./scripts/generate_qa_profile_data.sh

# Or specify endpoint explicitly
./scripts/generate_qa_profile_data.sh --endpoint https://<your-backend>.azurecontainerapps.io
```

### Using Python directly

```bash
python scripts/generate_qa_profile_data.py \
  --endpoint https://<your-backend>.azurecontainerapps.io \
  --count 100 \
  --output profile_data/voicerag_qa.jsonl
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--endpoint` | From env | VoiceRAG backend URL |
| `--count` | 100 | Number of Q&A pairs |
| `--output` | profile_data/voicerag_qa.jsonl | Output JSONL path |
| `--data-dir` | data | Source documents directory |
| `--skip-audio` | false | Skip audio generation (text only) |

### Environment variables

- `VOICERAG_ENDPOINT` or `BACKEND_URI` – Backend API URL
- `AZURE_OPENAI_ENDPOINT` – For question generation and local TTS
- `AZURE_OPENAI_API_KEY` – Or use Azure identity
- `AZURE_OPENAI_CHAT_DEPLOYMENT` – Chat model (default: gpt-5-mini)
- `AZURE_OPENAI_TTS_DEPLOYMENT` – TTS model for question audio (default: tts-hd)
- `AZURE_OPENAI_TTS_ENDPOINT` – Separate Azure OpenAI endpoint for TTS (if different from main endpoint)
- `AZURE_OPENAI_TTS_API_KEY` – API key for TTS endpoint (if different from main endpoint)
- `AZURE_OPENAI_REALTIME_VOICE_CHOICE` – Voice for TTS (default: alloy)
- `SSL_VERIFY` – Set to `false` to disable SSL certificate verification (default: `true`, use only for development/testing)

## Output Format

The JSONL file uses **HuggingFace chat format** with one JSON object per line:

```json
{
  "id": 1,
  "messages": [
    {"role": "user", "content": "What is the whistleblower policy?"},
    {"role": "assistant", "content": "The whistleblower policy..."}
  ],
  "question": "What is the whistleblower policy?",
  "answer": "The whistleblower policy...",
  "question_audio": "profile_data/audio/questions/q_0001.mp3",
  "answer_audio": "profile_data/audio/answers/a_0001.mp3"
}
```

The `messages` array follows the HuggingFace/AutoTrain chat format for instruction fine-tuning. The `question` and `answer` fields are included for convenience.

## WebSocket API Requirement

The script uses the VoiceRAG backend's WebSocket endpoint:

- `WS /realtime` – Real-time audio conversation with RAG (realtime-mini)

The script connects to this WebSocket endpoint to get both text answers and audio responses from the realtime-mini model. Ensure your deployment includes the WebSocket endpoint at `/realtime`.
