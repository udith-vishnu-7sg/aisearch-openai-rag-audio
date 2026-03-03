#!/bin/sh
# Generate Q&A profile data from VoiceRAG documents
# Requires: .venv with dependencies
# Set VOICERAG_ENDPOINT or use --endpoint URL
# Script auto-loads from .azure/voicerag-aisearch-openai-rag-audio/.env (BACKEND_URI) if present

. ./scripts/load_python_env.sh 2>/dev/null || true

./.venv/bin/python scripts/generate_qa_profile_data.py "$@"
