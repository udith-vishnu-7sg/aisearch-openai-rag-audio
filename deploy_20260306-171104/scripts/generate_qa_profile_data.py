#!/usr/bin/env python3
"""
Generate Q&A pairs from documents in the data folder.
- Generates questions using Azure OpenAI
- Creates question audio files locally using Azure OpenAI TTS
- Gets answers and audio from VoiceRAG app via WebSocket /realtime endpoint (realtime-mini)
- Outputs JSONL file in profile_data/ suitable for HuggingFace datasets

Usage:
    python scripts/generate_qa_profile_data.py [--endpoint URL] [--count N] [--output FILE]

Environment:
    VOICERAG_ENDPOINT - Backend API URL (default: from .env or https://...)
    Or use --endpoint flag
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import shutil
import ssl
import sys
from pathlib import Path

# Add app/backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "backend"))

import io

import websockets
from dotenv import load_dotenv
from openai import AzureOpenAI
from pydub import AudioSegment
from pypdf import PdfReader
from requests import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("generate_qa_profile")

# Suppress verbose Azure Identity/Core logs (EnvironmentCredential, ManagedIdentityCredential, IMDS)
for _logger in ("azure.identity", "azure.core", "urllib3.connectionpool"):
    logging.getLogger(_logger).setLevel(logging.WARNING)


def load_document_content(data_dir: Path) -> str:
    """Load and concatenate text content from all documents in data folder."""
    content_parts = []
    for file_path in sorted(data_dir.iterdir()):
        if file_path.is_file() and not file_path.name.startswith("."):
            try:
                if file_path.suffix.lower() == ".md":
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                elif file_path.suffix.lower() == ".pdf":
                    reader = PdfReader(file_path)
                    text = "\n".join(
                        page.extract_text() or "" for page in reader.pages
                    )
                else:
                    continue
                if text.strip():
                    content_parts.append(f"--- {file_path.name} ---\n{text}")
            except Exception as e:
                logger.warning(f"Could not read {file_path.name}: {e}")
    return "\n\n".join(content_parts)


def generate_questions(
    client: AzureOpenAI,
    document_content: str,
    count: int,
    deployment: str = "gpt-5-mini",
) -> list[str]:
    """Use Azure OpenAI to generate diverse questions based on document content."""
    # Truncate if very long (keep first ~50k chars)
    max_chars = 50000
    if len(document_content) > max_chars:
        document_content = document_content[:max_chars] + "\n\n[... truncated ...]"

    prompt = f"""Based on the following document content, generate exactly {count} diverse questions that a user might ask about this information.
Each question should be answerable from the documents. Vary the question types: factual, procedural, comparison, policy-related, etc.
Return ONLY the questions, one per line, numbered 1-{count}. No other text.

Document content:
{document_content}
"""

    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content or ""
    questions = []
    for line in text.strip().split("\n"):
        line = line.strip()
        # Remove leading number (e.g., "1. " or "1)")
        if line:
            for sep in (". ", ") ", ": "):
                if sep in line and line.split(sep)[0].strip().isdigit():
                    line = line.split(sep, 1)[1].strip()
                    break
            questions.append(line)
    return questions[:count]


def check_ffmpeg_available() -> bool:
    """Check if ffmpeg is available in the system PATH."""
    return shutil.which("ffmpeg") is not None


def convert_pcm_to_mp3(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Convert PCM16 audio data to MP3 format.
    
    Args:
        pcm_data: Raw PCM16 audio bytes
        sample_rate: Sample rate in Hz (default: 24000 for realtime-mini)
        channels: Number of audio channels (default: 1 for mono)
        sample_width: Sample width in bytes (default: 2 for 16-bit)
    
    Returns:
        MP3 audio data as bytes
    
    Raises:
        FileNotFoundError: If ffmpeg is not installed
        Exception: If conversion fails for other reasons
    """
    if not check_ffmpeg_available():
        raise FileNotFoundError(
            "ffmpeg is not installed or not in PATH. "
            "Please install ffmpeg:\n"
            "  macOS: brew install ffmpeg\n"
            "  Linux: sudo apt-get install ffmpeg\n"
            "  Windows: Download from https://ffmpeg.org/download.html"
        )
    
    try:
        # Create AudioSegment from raw PCM data
        audio = AudioSegment(
            pcm_data,
            frame_rate=sample_rate,
            channels=channels,
            sample_width=sample_width
        )
        # Export to MP3
        mp3_buffer = io.BytesIO()
        audio.export(mp3_buffer, format="mp3", bitrate="128k")
        return mp3_buffer.getvalue()
    except FileNotFoundError:
        # Re-raise FileNotFoundError with our custom message
        raise
    except Exception as e:
        logger.error(f"Error converting PCM to MP3: {e}", exc_info=True)
        raise


def call_api(
    session: Session,
    base_url: str,
    path: str,
    json_data: dict | None = None,
    raw_data: bytes | None = None,
) -> dict | bytes:
    """Call the VoiceRAG REST API."""
    url = f"{base_url.rstrip('/')}{path}"
    if json_data is not None:
        resp = session.post(url, json=json_data, timeout=120)
    elif raw_data is not None:
        resp = session.post(url, data=raw_data, timeout=120)
    else:
        resp = session.post(url, json={}, timeout=120)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return resp.json()
    return resp.content


async def get_realtime_response(
    endpoint: str,
    question_text: str,
) -> tuple[str, bytes]:
    """Connect to WebSocket /realtime endpoint and capture both text and audio response.
    
    Returns:
        tuple: (response_text, audio_data)
    """
    # Convert HTTP endpoint to WebSocket (app's /realtime endpoint proxies to Azure OpenAI)
    ws_url = endpoint.replace("https://", "wss://").replace("http://", "ws://") + "/realtime"
    
    # App's WebSocket endpoint doesn't require auth headers (it handles auth internally)
    # Handle SSL verification - Azure Container Apps should have valid certs, but allow disabling for dev/testing
    ssl_context = None
    ssl_verify = os.environ.get("SSL_VERIFY", "true").lower()
    if ssl_verify == "false":
        logger.warning("SSL verification disabled (SSL_VERIFY=false) - use only for development/testing")
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    elif "azurecontainerapps.io" in endpoint.lower():
        # For Azure Container Apps, use default SSL context (should work with valid certs)
        ssl_context = ssl.create_default_context()
    # For other endpoints, use default (None = default SSL context)
    
    audio_chunks = []
    response_text = ""
    
    async with websockets.connect(ws_url, ssl=ssl_context) as ws:
        logger.info("WebSocket connected, starting session...")
        # Start session - enable audio and text output
        session_update = {
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "voice": os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE", "alloy"),
                "modalities": ["text", "audio"],  # Explicitly enable both text and audio
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16"
            }
        }
        await ws.send(json.dumps(session_update))
        logger.info("Sent session.update")
        
        # Wait for session.created
        session_created = False
        while not session_created:
            msg = await ws.recv()
            message = json.loads(msg)
            msg_type = message.get("type")
            logger.info(f"Waiting for session.created, received: {msg_type}")
            if msg_type == "session.created":
                session_created = True
                logger.info("Session created successfully")
        
        # Send question as text input
        logger.info(f"Sending question: {question_text[:100]}...")
        question_msg = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": question_text}]
            }
        }
        await ws.send(json.dumps(question_msg))
        logger.info("Sent conversation.item.create")
        
        # Request response
        await ws.send(json.dumps({"type": "response.create"}))
        logger.info("Sent response.create, waiting for response...")
        
        # Collect audio chunks and transcript, wait for final response.done
        # Note: There may be multiple responses (tool calls, then final answer)
        response_done = False
        timeout_count = 0
        max_timeout = 60  # 60 seconds timeout
        all_messages = []  # Store all messages for debugging
        response_count = 0  # Track number of responses
        
        while not response_done:
            try:
                # Set a timeout for receiving messages
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    timeout_count += 1
                    if timeout_count >= max_timeout:
                        logger.warning("WebSocket timeout waiting for response.done")
                        break
                    continue
                
                message = json.loads(msg)
                msg_type = message.get("type")
                all_messages.append(msg_type)
                
                # Log all messages for debugging
                if msg_type in ["response.audio.delta", "response.audio_transcript.delta"]:
                    logger.info(f"WebSocket message received: {msg_type} (THIS IS WHAT WE'RE LOOKING FOR!)")
                else:
                    logger.info(f"WebSocket message received: {msg_type}")
                
                if msg_type == "response.audio.delta":
                    delta = message.get("delta", "")
                    if delta:
                        decoded = base64.b64decode(delta)
                        audio_chunks.append(decoded)
                        logger.info(f"Audio delta received: {len(decoded)} bytes (total: {sum(len(c) for c in audio_chunks)} bytes)")
                
                elif msg_type == "response.audio_transcript.delta":
                    # Capture the transcript
                    delta = message.get("delta", "")
                    if delta:
                        response_text += delta
                        logger.info(f"Transcript delta: {delta[:100]}...")
                
                elif msg_type == "response.done":
                    response_count += 1
                    logger.info(f"Received response.done #{response_count} - checking if this is the final response...")
                    # Check if response contains final text or audio
                    if "response" in message:
                        resp = message["response"]
                        output_items = resp.get("output", [])
                        logger.info(f"Response #{response_count} output has {len(output_items)} items")
                        
                        # Check if this response has actual content (not just tool calls)
                        has_content = False
                        for output_item in output_items:
                            item_type = output_item.get("type", "")
                            if item_type not in ["function_call"]:  # Ignore function_call items
                                has_content = True
                                logger.info(f"Response #{response_count} has content: {item_type}")
                                # Check for transcript in output_item
                                if "content" in output_item:
                                    for content_item in output_item["content"]:
                                        if content_item.get("type") == "audio_transcript" and "transcript" in content_item:
                                            final_transcript = content_item["transcript"]
                                            if final_transcript:
                                                response_text = final_transcript
                                                logger.info(f"Found final transcript in response.done: {final_transcript[:100]}...")
                                elif output_item.get("type") == "audio_transcript" and "transcript" in output_item:
                                    final_transcript = output_item["transcript"]
                                    if final_transcript:
                                        response_text = final_transcript
                                        logger.info(f"Found final transcript in response.done (direct): {final_transcript[:100]}...")
                        
                        # If this response has content (audio/transcript), it's likely the final one
                        # Otherwise, wait for another response (tool calls completed, now generating answer)
                        if has_content or len(output_items) == 0:
                            # This might be the final response, but check if we have audio/transcript
                            # Also check if we've received any audio/transcript deltas
                            if len(audio_chunks) > 0 or len(response_text) > 0:
                                logger.info(f"Response #{response_count} appears to be final (has audio/transcript)")
                                response_done = True
                            elif response_count >= 3:
                                # We've had multiple responses, assume this is final even if empty
                                logger.warning(f"Response #{response_count} is final but has no content - may indicate an error")
                                logger.warning(f"Full response structure: {json.dumps(resp, indent=2)}")
                                response_done = True
                            else:
                                logger.info(f"Response #{response_count} completed but no content yet, waiting for next response...")
                                logger.info(f"Will wait up to {max_timeout - timeout_count} more seconds for audio/transcript")
                        else:
                            logger.info(f"Response #{response_count} only has tool calls, waiting for final response...")
                    
                    if response_done:
                        # Log summary of all messages received
                        logger.info(f"Final response completed after {response_count} responses")
                        logger.info(f"All message types received: {', '.join(all_messages)}")
                        logger.info(f"Audio chunks collected: {len(audio_chunks)}, total bytes: {sum(len(c) for c in audio_chunks)}")
                        logger.info(f"Transcript length: {len(response_text)} chars")
                
                elif msg_type == "error":
                    error_msg = message.get("error", {}).get("message", "Unknown error")
                    logger.error(f"WebSocket error message: {error_msg}")
                    break
                
                elif msg_type == "response.output_item.added":
                    # Check if this is an audio_transcript or audio item
                    if "item" in message:
                        item = message["item"]
                        item_type = item.get("type")
                        logger.info(f"response.output_item.added: type={item_type}, item={json.dumps(item)[:300]}")
                        if item_type == "audio_transcript":
                            if "transcript" in item:
                                response_text = item["transcript"]
                                logger.info(f"Found transcript in output_item.added: {response_text[:100]}...")
                        elif item_type == "audio":
                            # Audio item might contain audio data
                            logger.info(f"Found audio item in output_item.added")
                
                elif msg_type == "response.output_item.done":
                    # Check if this completes an audio_transcript item
                    if "item" in message:
                        item = message["item"]
                        item_type = item.get("type")
                        logger.info(f"response.output_item.done: type={item_type}, item={json.dumps(item)[:300]}")
                        if item_type == "audio_transcript" and "transcript" in item:
                            response_text = item["transcript"]
                            logger.info(f"Found transcript in output_item.done: {response_text[:100]}...")
                
                elif msg_type == "extension.middle_tier_tool_response":
                    # Tool response from backend - log it
                    tool_name = message.get("tool_name", "unknown")
                    logger.info(f"Tool response received: {tool_name}")
                
                elif msg_type == "response.created":
                    # A new response is being created (could be after tool calls)
                    logger.info(f"Response created (this might be response #{response_count + 1} after tool calls)")
                
                elif msg_type in ["conversation.item.created", 
                                  "response.function_call_arguments.delta",
                                  "response.function_call_arguments.done",
                                  "session.updated"]:
                    # These are expected but we don't need to handle them (tool calls, etc.)
                    logger.debug(f"Ignoring message type: {msg_type}")
                
                else:
                    logger.info(f"Unhandled message type: {msg_type}, keys: {list(message.keys())}, sample: {json.dumps(message)[:300]}")
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                break
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse WebSocket message as JSON: {e}")
                continue
            except Exception as e:
                logger.warning(f"WebSocket error: {e}", exc_info=True)
                break
    
    # Concatenate all audio chunks
    audio_data = b"".join(audio_chunks) if audio_chunks else b""
    return response_text.strip(), audio_data


def main():
    # Check for ffmpeg at startup and warn if not available
    if not check_ffmpeg_available():
        logger.warning(
            "ffmpeg is not installed or not in PATH. Answer audio will be saved as PCM instead of MP3.\n"
            "To enable MP3 conversion, install ffmpeg:\n"
            "  macOS: brew install ffmpeg\n"
            "  Linux: sudo apt-get install ffmpeg\n"
            "  Windows: Download from https://ffmpeg.org/download.html"
        )
    
    parser = argparse.ArgumentParser(
        description="Generate Q&A profile data from VoiceRAG documents"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("VOICERAG_ENDPOINT"),
        help="VoiceRAG backend URL (e.g., https://capps-backend-xxx.azurecontainerapps.io)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of Q&A pairs to generate (default: 100)",
    )
    parser.add_argument(
        "--output",
        default="profile_data/voicerag_qa.jsonl",
        help="Output JSONL file path (default: profile_data/voicerag_qa.jsonl)",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing source documents (default: data)",
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Skip generating audio files (only text Q&A)",
    )
    args = parser.parse_args()

    # Load env: azd first (has BACKEND_URI), then app/backend/.env
    project_root = Path(__file__).resolve().parent.parent
    azd_env = project_root / ".azure" / "voicerag-aisearch-openai-rag-audio" / ".env"
    if azd_env.exists():
        load_dotenv(azd_env)
    env_path = project_root / "app" / "backend" / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    endpoint = args.endpoint
    if not endpoint:
        endpoint = os.environ.get("VOICERAG_ENDPOINT") or os.environ.get("BACKEND_URI")
        if not endpoint:
            logger.error(
                "No endpoint specified. Set VOICERAG_ENDPOINT or BACKEND_URI, "
                "or use --endpoint <your-backend-url>"
            )
            sys.exit(1)

    endpoint = endpoint.rstrip("/")
    if "xxx" in endpoint.lower():
        logger.error(
            "Endpoint appears to be a placeholder (contains 'xxx'). "
            "Use your actual backend URL or omit --endpoint to use BACKEND_URI from .azure env."
        )
        sys.exit(1)
    logger.info(f"Using endpoint: {endpoint}")

    # Paths
    data_dir = project_root / args.data_dir
    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Delete files from previous run
    if output_path.exists():
        output_path.unlink()
        logger.info(f"Removed previous output: {output_path}")
    audio_dir = output_path.parent / "audio"
    for subdir in ("questions", "answers"):
        subdir_path = audio_dir / subdir
        if subdir_path.exists():
            for f in subdir_path.iterdir():
                if f.is_file():
                    f.unlink()
            logger.info(f"Cleared previous audio: {subdir_path}")

    # Create audio subdirs
    question_audio_dir = audio_dir / "questions"
    answer_audio_dir = audio_dir / "answers"
    if not args.skip_audio:
        question_audio_dir.mkdir(parents=True, exist_ok=True)
        answer_audio_dir.mkdir(parents=True, exist_ok=True)

    # Load documents
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        sys.exit(1)
    document_content = load_document_content(data_dir)
    if not document_content.strip():
        logger.error("No document content found")
        sys.exit(1)
    logger.info(f"Loaded {len(document_content)} chars from documents")

    # Initialize Azure OpenAI client for question generation
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        client = AzureOpenAI(
            azure_ad_token_provider=token_provider,
            api_version="2024-02-15-preview",
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        )
    else:
        client = AzureOpenAI(
            api_key=api_key,
            api_version="2024-02-15-preview",
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        )

    # Initialize separate Azure OpenAI client for TTS if TTS endpoint is different
    tts_endpoint = os.environ.get("AZURE_OPENAI_TTS_ENDPOINT")
    tts_api_key = os.environ.get("AZURE_OPENAI_TTS_API_KEY")
    tts_client = None
    if tts_endpoint:
        logger.info(f"Using separate TTS endpoint: {tts_endpoint}")
        # Use TTS-specific API key if provided, otherwise use main API key or credential
        if tts_api_key:
            tts_client = AzureOpenAI(
                api_key=tts_api_key,
                api_version="2025-03-01-preview",
                azure_endpoint=tts_endpoint,
            )
        elif api_key:
            tts_client = AzureOpenAI(
                api_key=api_key,
                api_version="2025-03-01-preview",
                azure_endpoint=tts_endpoint,
            )
        else:
            tts_token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            tts_client = AzureOpenAI(
                azure_ad_token_provider=tts_token_provider,
                api_version="2025-03-01-preview",
                azure_endpoint=tts_endpoint,
            )

    deployment = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5-mini")
    questions = generate_questions(
        client, document_content, args.count, deployment
    )
    logger.info(f"Generated {len(questions)} questions")

    records = []
    for i, question in enumerate(questions):
        if not question.strip():
            continue
        try:
            question_audio_path = None
            answer_audio_path = None

            # Step 1: Generate and save question audio locally BEFORE calling app
            if not args.skip_audio:
                try:
                    tts_deployment = os.environ.get("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-hd")
                    logger.info(f"[{i + 1}] Generating question audio locally...")
                    # Use TTS client if separate endpoint configured, otherwise use main client
                    tts_client_to_use = tts_client if tts_client else client
                    tts_response = tts_client_to_use.audio.speech.create(
                        model=tts_deployment,
                        voice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE", "alloy"),
                        input=question,
                    )
                    # Read the binary response content (synchronous response has .content attribute)
                    q_audio_data = tts_response.content if hasattr(tts_response, 'content') else b""
                    if q_audio_data:
                        q_audio_path = question_audio_dir / f"q_{i + 1:04d}.mp3"
                        q_audio_path.write_bytes(q_audio_data)
                        question_audio_path = str(q_audio_path.relative_to(project_root))
                        logger.info(f"[{i + 1}] Saved question audio (MP3): {len(q_audio_data)} bytes")
                except Exception as e:
                    logger.warning(f"[{i + 1}] Failed to generate question audio locally: {e}")

            # Step 2: Get answer text and audio from app via WebSocket /realtime endpoint
            logger.info(f"[{i + 1}] Getting answer from app via WebSocket...")
            try:
                # Connect to WebSocket and get both text and audio response
                # Note: App's /realtime endpoint handles auth internally, no headers needed
                answer, a_audio_data = asyncio.run(
                    get_realtime_response(
                        endpoint=endpoint,
                        question_text=question,
                    )
                )
                logger.info(f"[{i + 1}] Received answer from app: {len(answer)} chars, {len(a_audio_data)} audio bytes")
                
                # Step 3: Save answer audio if available (realtime-mini returns PCM audio, convert to MP3)
                if not args.skip_audio and a_audio_data and len(a_audio_data) > 0:
                    try:
                        # Convert PCM16 to MP3 (realtime-mini uses 24kHz, 16-bit, mono PCM)
                        mp3_data = convert_pcm_to_mp3(a_audio_data, sample_rate=24000, channels=1, sample_width=2)
                        a_audio_path = answer_audio_dir / f"a_{i + 1:04d}.mp3"
                        a_audio_path.write_bytes(mp3_data)
                        answer_audio_path = str(a_audio_path.relative_to(project_root))
                        logger.info(f"[{i + 1}] Saved answer audio from app (MP3): {len(a_audio_data)} bytes PCM -> {len(mp3_data)} bytes MP3")
                    except FileNotFoundError as e:
                        # ffmpeg not installed - save as PCM and warn user
                        logger.error(f"[{i + 1}] {e}")
                        a_audio_path = answer_audio_dir / f"a_{i + 1:04d}.pcm"
                        a_audio_path.write_bytes(a_audio_data)
                        answer_audio_path = str(a_audio_path.relative_to(project_root))
                        logger.warning(f"[{i + 1}] Saved answer audio as PCM (ffmpeg not available): {len(a_audio_data)} bytes")
                        logger.warning(f"[{i + 1}] Install ffmpeg to enable MP3 conversion: brew install ffmpeg")
                    except Exception as e:
                        logger.error(f"[{i + 1}] Failed to convert PCM to MP3: {e}", exc_info=True)
                        # Fallback: save as PCM
                        a_audio_path = answer_audio_dir / f"a_{i + 1:04d}.pcm"
                        a_audio_path.write_bytes(a_audio_data)
                        answer_audio_path = str(a_audio_path.relative_to(project_root))
                        logger.warning(f"[{i + 1}] Saved answer audio as PCM (conversion failed): {len(a_audio_data)} bytes")
                elif not args.skip_audio:
                    logger.warning(f"[{i + 1}] No audio data received from WebSocket (0 bytes)")
                    
            except Exception as e:
                logger.error(f"[{i + 1}] Failed to get answer from app WebSocket: {e}", exc_info=True)
                answer = ""  # Fallback to empty answer on error

            # Step 4: Create record with all data
            record = {
                "id": i + 1,
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
                "question": question,
                "answer": answer,
            }
            if question_audio_path:
                record["question_audio"] = question_audio_path
            if answer_audio_path:
                record["answer_audio"] = answer_audio_path

            records.append(record)
            logger.info(f"[{i + 1}/{len(questions)}] Q&A recorded")

        except Exception as e:
            logger.error(f"[{i + 1}] Failed: {e}")
            records.append({
                "id": i + 1,
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": ""},
                ],
                "question": question,
                "answer": "",
                "error": str(e),
            })

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info(f"Wrote {len(records)} records to {output_path}")
    logger.info("JSONL format: messages (HuggingFace chat format), question, answer")


if __name__ == "__main__":
    main()
