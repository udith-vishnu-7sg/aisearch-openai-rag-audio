import base64
import io
import logging
import struct
from typing import Optional

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI

logger = logging.getLogger("voicerag")


class MiniAPI:
    """Handles transcription using gpt-realtime-mini, chat completion, and TTS."""
    
    def __init__(
        self,
        endpoint: str,
        realtime_deployment: str,
        chat_deployment: str,
        credentials: AzureKeyCredential | DefaultAzureCredential,
        voice_choice: Optional[str] = None,
        tts_deployment: Optional[str] = None,
    ):
        self.endpoint = endpoint
        self.realtime_deployment = realtime_deployment
        self.chat_deployment = chat_deployment
        self.voice_choice = voice_choice or "alloy"
        # TTS requires tts-1 or tts-1-hd; gpt-realtime-mini does not support audio.speech.create
        self.tts_deployment = tts_deployment or realtime_deployment
        
        # Initialize Azure OpenAI client
        if isinstance(credentials, AzureKeyCredential):
            self.client = AsyncAzureOpenAI(
                api_key=credentials.key,
                api_version="2024-02-15-preview",
                azure_endpoint=endpoint,
            )
        else:
            token_provider = get_bearer_token_provider(
                credentials, "https://cognitiveservices.azure.com/.default"
            )
            self.client = AsyncAzureOpenAI(
                azure_ad_token_provider=token_provider,
                api_version="2024-02-15-preview",
                azure_endpoint=endpoint,
            )
        
        self.conversation_history = []
        self.system_message = None
        self.tools = {}
        
    def set_system_message(self, message: str):
        """Set the system message for chat completions."""
        self.system_message = message
        
    def add_tool(self, name: str, schema: dict, target):
        """Add a tool for function calling."""
        self.tools[name] = {"schema": schema, "target": target}
        
    def clear_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []
        
    def _pcm_to_wav(self, pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
        """Convert PCM audio data to WAV format."""
        # WAV header structure
        wav_header = b'RIFF'
        wav_header += struct.pack('<I', 0)  # File size (will be filled later)
        wav_header += b'WAVE'
        
        # fmt chunk
        wav_header += b'fmt '
        wav_header += struct.pack('<I', 16)  # fmt chunk size
        wav_header += struct.pack('<H', 1)   # audio format (1 = PCM)
        wav_header += struct.pack('<H', channels)
        wav_header += struct.pack('<I', sample_rate)
        wav_header += struct.pack('<I', sample_rate * channels * sample_width)  # byte rate
        wav_header += struct.pack('<H', channels * sample_width)  # block align
        wav_header += struct.pack('<H', sample_width * 8)  # bits per sample
        
        # data chunk
        wav_header += b'data'
        wav_header += struct.pack('<I', len(pcm_data))
        
        # Combine header and data
        wav_file = wav_header + pcm_data
        
        # Update file size in header
        file_size = len(wav_file) - 8
        wav_file = wav_file[:4] + struct.pack('<I', file_size) + wav_file[8:]
        
        return wav_file
    
    async def transcribe_audio(self, audio_data: bytes) -> str:
        """Transcribe audio using gpt-realtime-mini API."""
        try:
            logger.info(f"[MiniAPI] Starting transcription, audio size: {len(audio_data)} bytes")
            logger.info(f"[MiniAPI] Using endpoint: {self.endpoint}, deployment: {self.realtime_deployment}")
            
            # Convert PCM to WAV format
            try:
                wav_data = self._pcm_to_wav(audio_data)
                logger.info(f"[MiniAPI] Converted to WAV, size: {len(wav_data)} bytes")
            except Exception as wav_error:
                logger.error(f"[MiniAPI] Error converting PCM to WAV: {wav_error}", exc_info=True)
                raise web.HTTPInternalServerError(text=f"Audio conversion failed: {str(wav_error)}")
            
            # Create a file-like object from the WAV data
            audio_file = io.BytesIO(wav_data)
            audio_file.name = "audio.wav"
            
            # Call transcription API
            logger.info(f"[MiniAPI] Calling transcription API with model: {self.realtime_deployment}")
            try:
                transcription = await self.client.audio.transcriptions.create(
                    model=self.realtime_deployment,
                    file=audio_file,
                )
                logger.info(f"[MiniAPI] Transcription API call successful")
            except Exception as api_error:
                error_type = type(api_error).__name__
                logger.error(f"[MiniAPI] Transcription API error ({error_type}): {api_error}", exc_info=True)
                # Check for common errors
                if "deployment" in str(api_error).lower() or "model" in str(api_error).lower():
                    raise web.HTTPInternalServerError(
                        text=f"Model deployment '{self.realtime_deployment}' not found or not accessible. "
                             f"Error: {str(api_error)}"
                    )
                elif "authentication" in str(api_error).lower() or "unauthorized" in str(api_error).lower():
                    raise web.HTTPInternalServerError(
                        text=f"Authentication failed. Check credentials. Error: {str(api_error)}"
                    )
                else:
                    raise web.HTTPInternalServerError(text=f"Transcription API error: {str(api_error)}")
            
            if not hasattr(transcription, 'text') or not transcription.text:
                logger.warn("[MiniAPI] Transcription returned empty text")
                return ""
            
            logger.info(f"[MiniAPI] Transcription successful: {transcription.text}")
            return transcription.text
        except web.HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            logger.error(f"[MiniAPI] Unexpected error transcribing audio: {e}", exc_info=True)
            raise web.HTTPInternalServerError(text=f"Transcription failed: {str(e)}")
    
    async def chat_completion(self, user_message: str) -> dict:
        """Get chat completion with RAG tools."""
        try:
            logger.info(f"[MiniAPI] Starting chat completion, user message: {user_message}")
            # Add user message to conversation history
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            
            # Prepare messages
            messages = []
            if self.system_message:
                messages.append({
                    "role": "system",
                    "content": self.system_message
                })
            messages.extend(self.conversation_history)
            
            # Prepare tools if available
            tools = None
            if self.tools:
                tools = [tool["schema"] for tool in self.tools.values()]
                logger.info(f"[MiniAPI] Using {len(tools)} tools: {list(self.tools.keys())}")
            
            # Call chat completion API
            logger.info(f"[MiniAPI] Calling chat completion API with model: {self.chat_deployment}")
            response = await self.client.chat.completions.create(
                model=self.chat_deployment,
                messages=messages,
                tools=tools,
                tool_choice="auto" if tools else None,
            )
            logger.info(f"[MiniAPI] Chat completion API response received")
            
            # Handle function calls
            assistant_message = response.choices[0].message
            tool_results = []
            
            if assistant_message.tool_calls:
                logger.info(f"[MiniAPI] Assistant requested {len(assistant_message.tool_calls)} tool calls")
                # Add assistant message with tool calls
                self.conversation_history.append({
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in assistant_message.tool_calls
                    ]
                })
                
                # Execute tool calls
                for tool_call in assistant_message.tool_calls:
                    tool_name = tool_call.function.name
                    logger.info(f"[MiniAPI] Executing tool: {tool_name}")
                    if tool_name in self.tools:
                        import json
                        args = json.loads(tool_call.function.arguments)
                        logger.info(f"[MiniAPI] Tool {tool_name} arguments: {args}")
                        tool_result = await self.tools[tool_name]["target"](args)
                        logger.info(f"[MiniAPI] Tool {tool_name} execution completed")
                        
                        # Add tool result to conversation
                        tool_content = tool_result.to_text() if hasattr(tool_result, "to_text") else str(tool_result)
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": tool_content,
                        })
                        
                        # Store tool result - handle ToolResult objects
                        tool_result_data = tool_result
                        if hasattr(tool_result, "destination"):
                            from rtmt import ToolResultDirection
                            # If it's a ToolResult with TO_CLIENT direction, serialize it properly
                            if tool_result.destination == ToolResultDirection.TO_CLIENT:
                                # For TO_CLIENT, we need to return the actual data structure
                                if hasattr(tool_result, "text") and isinstance(tool_result.text, dict):
                                    tool_result_data = tool_result.text
                                else:
                                    try:
                                        tool_result_data = json.loads(tool_result.to_text())
                                    except:
                                        tool_result_data = {"content": tool_result.to_text()}
                            else:
                                # For TO_SERVER, just use the text representation
                                tool_result_data = {"content": tool_result.to_text()}
                        
                        tool_results.append({
                            "name": tool_name,
                            "result": tool_result_data,
                        })
                
                # Get final response after tool execution
                logger.info("[MiniAPI] Getting final response after tool execution")
                messages = []
                if self.system_message:
                    messages.append({
                        "role": "system",
                        "content": self.system_message
                    })
                messages.extend(self.conversation_history)
                
                response = await self.client.chat.completions.create(
                    model=self.chat_deployment,
                    messages=messages,
                )
                assistant_message = response.choices[0].message
                logger.info(f"[MiniAPI] Final response received: {assistant_message.content}")
            
            # Add assistant response to conversation history
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message.content
            })
            
            logger.info(f"[MiniAPI] Chat completion successful, returning response")
            return {
                "text": assistant_message.content,
                "tool_results": tool_results,
            }
            
        except Exception as e:
            logger.error(f"[MiniAPI] Error in chat completion: {e}", exc_info=True)
            raise web.HTTPInternalServerError(text=f"Chat completion failed: {str(e)}")
    
    async def synthesize_speech(self, text: str) -> bytes:
        """Synthesize speech using Azure OpenAI TTS API (requires tts-1 or tts-1-hd deployment)."""
        try:
            logger.info(f"[MiniAPI] Starting speech synthesis, text: {text[:100]}...")
            logger.info(f"[MiniAPI] Using TTS model: {self.tts_deployment}, voice: {self.voice_choice}")
            response = await self.client.audio.speech.create(
                model=self.tts_deployment,
                voice=self.voice_choice,
                input=text,
            )
            
            # Read the audio data - response is a streamable object
            audio_data = b""
            chunk_count = 0
            async for chunk in response:
                audio_data += chunk
                chunk_count += 1
            
            logger.info(f"[MiniAPI] Speech synthesis successful, audio size: {len(audio_data)} bytes, chunks: {chunk_count}")
            return audio_data
            
        except Exception as e:
            logger.error(f"[MiniAPI] Error synthesizing speech: {e}", exc_info=True)
            # If TTS fails, return empty audio
            return b""
    
    async def transcribe_handler(self, request: web.Request) -> web.Response:
        """Handle POST /transcribe endpoint."""
        try:
            logger.info("[MiniAPI] /transcribe endpoint called")
            # Get audio data from request
            data = await request.read()
            logger.info(f"[MiniAPI] Received {len(data)} bytes in request")
            
            # Decode base64 if needed
            try:
                audio_data = base64.b64decode(data)
                logger.info(f"[MiniAPI] Decoded base64, audio size: {len(audio_data)} bytes")
            except Exception as decode_error:
                logger.warn(f"[MiniAPI] Base64 decode failed: {decode_error}, using data as-is")
                audio_data = data
                logger.info(f"[MiniAPI] Using raw data, size: {len(audio_data)} bytes")
            
            # Validate audio data
            if len(audio_data) == 0:
                error_msg = "Empty audio data received"
                logger.error(f"[MiniAPI] {error_msg}")
                return web.json_response({"error": error_msg}, status=400)
            
            # Transcribe
            text = await self.transcribe_audio(audio_data)
            
            logger.info(f"[MiniAPI] /transcribe endpoint returning: {text}")
            return web.json_response({"text": text})
            
        except web.HTTPException:
            # Re-raise HTTP exceptions (they already have proper status codes)
            raise
        except Exception as e:
            error_msg = f"Transcription failed: {str(e)}"
            error_type = type(e).__name__
            logger.error(f"[MiniAPI] Transcribe handler error ({error_type}): {error_msg}", exc_info=True)
            # Return detailed error for debugging
            return web.json_response({
                "error": error_msg,
                "error_type": error_type,
                "details": str(e)
            }, status=500)
    
    async def chat_handler(self, request: web.Request) -> web.Response:
        """Handle POST /chat endpoint."""
        try:
            logger.info("[MiniAPI] /chat endpoint called")
            data = await request.json()
            user_message = data.get("message", "")
            
            if not user_message:
                logger.warn("[MiniAPI] /chat endpoint called without message")
                return web.json_response({"error": "Message is required"}, status=400)
            
            # Get chat completion
            result = await self.chat_completion(user_message)
            
            logger.info(f"[MiniAPI] /chat endpoint returning response")
            return web.json_response({
                "text": result["text"],
                "tool_results": result.get("tool_results", []),
            })
            
        except Exception as e:
            logger.error(f"[MiniAPI] Chat handler error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)
    
    async def synthesize_handler(self, request: web.Request) -> web.Response:
        """Handle POST /synthesize endpoint."""
        try:
            logger.info("[MiniAPI] /synthesize endpoint called")
            data = await request.json()
            text = data.get("text", "")
            
            if not text:
                logger.warn("[MiniAPI] /synthesize endpoint called without text")
                return web.json_response({"error": "Text is required"}, status=400)
            
            # Synthesize speech
            audio_data = await self.synthesize_speech(text)
            
            # Return as base64 encoded
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")
            logger.info(f"[MiniAPI] /synthesize endpoint returning audio, size: {len(audio_base64)} chars")
            
            return web.json_response({"audio": audio_base64})
            
        except Exception as e:
            logger.error(f"[MiniAPI] Synthesize handler error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)
    
    async def clear_handler(self, request: web.Request) -> web.Response:
        """Handle POST /clear endpoint to clear conversation history."""
        self.clear_conversation()
        return web.json_response({"status": "cleared"})
    
    def attach_to_app(self, app: web.Application):
        """Attach REST endpoints to the app."""
        app.router.add_post("/transcribe", self.transcribe_handler)
        app.router.add_post("/chat", self.chat_handler)
        app.router.add_post("/synthesize", self.synthesize_handler)
        app.router.add_post("/clear", self.clear_handler)

