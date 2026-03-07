import { useState, useCallback } from "react";

type Parameters = {
    onTranscriptionComplete?: (text: string) => void;
    onChatResponse?: (text: string, toolResults?: any[]) => void;
    onAudioReceived?: (audioBase64: string) => void;
    onError?: (error: string) => void;
};

export default function useMini({
    onTranscriptionComplete,
    onChatResponse,
    onAudioReceived,
    onError
}: Parameters) {
    const [isProcessing, setIsProcessing] = useState(false);

    const transcribeAudio = useCallback(async (audioBase64: string) => {
        try {
            console.log("[useMini] Starting transcription, audio size:", audioBase64.length, "chars");
            setIsProcessing(true);
            
            // Decode base64 to binary
            const binary = atob(audioBase64);
            const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
            console.log("[useMini] Decoded audio to", bytes.length, "bytes");
            
            // Send to backend for transcription
            console.log("[useMini] Sending transcription request to /transcribe");
            const response = await fetch("/transcribe", {
                method: "POST",
                headers: {
                    "Content-Type": "application/octet-stream",
                },
                body: bytes,
            });

            console.log("[useMini] Transcription response status:", response.status);
            if (!response.ok) {
                let errorData;
                try {
                    errorData = await response.json();
                } catch {
                    errorData = { error: `HTTP ${response.status}: ${response.statusText}` };
                }
                console.error("[useMini] Transcription failed:", errorData);
                // Include detailed error information if available
                const errorMsg = errorData.error || "Transcription failed";
                const errorType = errorData.error_type ? ` (${errorData.error_type})` : "";
                const details = errorData.details ? `\nDetails: ${errorData.details}` : "";
                throw new Error(`${errorMsg}${errorType}${details}`);
            }

            const data = await response.json();
            const transcribedText = data.text;
            console.log("[useMini] Transcription successful:", transcribedText);
            
            onTranscriptionComplete?.(transcribedText);
            return transcribedText;
        } catch (error: any) {
            const errorMessage = error.message || "Transcription failed";
            console.error("[useMini] Transcription error:", errorMessage, error);
            onError?.(errorMessage);
            throw error;
        } finally {
            setIsProcessing(false);
        }
    }, [onTranscriptionComplete, onError]);

    const sendMessage = useCallback(async (message: string) => {
        try {
            console.log("[useMini] Sending chat message:", message);
            setIsProcessing(true);
            
            console.log("[useMini] Sending chat request to /chat");
            const response = await fetch("/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ message }),
            });

            console.log("[useMini] Chat response status:", response.status);
            if (!response.ok) {
                let errorData;
                try {
                    errorData = await response.json();
                } catch {
                    errorData = { error: `HTTP ${response.status}: ${response.statusText}` };
                }
                console.error("[useMini] Chat failed:", errorData);
                const errorMsg = errorData.error || "Chat failed";
                const errorType = errorData.error_type ? ` (${errorData.error_type})` : "";
                const details = errorData.details ? `\nDetails: ${errorData.details}` : "";
                throw new Error(`${errorMsg}${errorType}${details}`);
            }

            const data = await response.json();
            const responseText = data.text;
            const toolResults = data.tool_results || [];
            console.log("[useMini] Chat response received:", responseText);
            if (toolResults.length > 0) {
                console.log("[useMini] Tool results:", toolResults);
            }
            
            onChatResponse?.(responseText, toolResults);
            return { text: responseText, toolResults };
        } catch (error: any) {
            const errorMessage = error.message || "Chat failed";
            console.error("[useMini] Chat error:", errorMessage, error);
            onError?.(errorMessage);
            throw error;
        } finally {
            setIsProcessing(false);
        }
    }, [onChatResponse, onError]);

    const synthesizeSpeech = useCallback(async (text: string) => {
        try {
            console.log("[useMini] Starting speech synthesis for text:", text);
            setIsProcessing(true);
            
            console.log("[useMini] Sending synthesis request to /synthesize");
            const response = await fetch("/synthesize", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ text }),
            });

            console.log("[useMini] Synthesis response status:", response.status);
            if (!response.ok) {
                let errorData;
                try {
                    errorData = await response.json();
                } catch {
                    errorData = { error: `HTTP ${response.status}: ${response.statusText}` };
                }
                console.error("[useMini] Speech synthesis failed:", errorData);
                const errorMsg = errorData.error || "Speech synthesis failed";
                const errorType = errorData.error_type ? ` (${errorData.error_type})` : "";
                const details = errorData.details ? `\nDetails: ${errorData.details}` : "";
                throw new Error(`${errorMsg}${errorType}${details}`);
            }

            const data = await response.json();
            const audioBase64 = data.audio;
            console.log("[useMini] Speech synthesis successful, audio size:", audioBase64.length, "chars");
            
            onAudioReceived?.(audioBase64);
            return audioBase64;
        } catch (error: any) {
            const errorMessage = error.message || "Speech synthesis failed";
            console.error("[useMini] Speech synthesis error:", errorMessage, error);
            onError?.(errorMessage);
            throw error;
        } finally {
            setIsProcessing(false);
        }
    }, [onAudioReceived, onError]);

    const clearConversation = useCallback(async () => {
        try {
            await fetch("/clear", {
                method: "POST",
            });
        } catch (error: any) {
            console.error("Failed to clear conversation:", error);
        }
    }, []);

    const processAudioToResponse = useCallback(async (audioBase64: string) => {
        try {
            console.log("[useMini] Starting processAudioToResponse pipeline");
            // Step 1: Transcribe audio
            console.log("[useMini] Step 1: Transcribing audio...");
            const transcribedText = await transcribeAudio(audioBase64);
            
            if (!transcribedText) {
                console.warn("[useMini] No transcription text received, stopping pipeline");
                return;
            }
            
            // Step 2: Get chat response
            console.log("[useMini] Step 2: Getting chat response...");
            const chatResult = await sendMessage(transcribedText);
            
            // Step 3: Synthesize speech
            if (chatResult.text) {
                console.log("[useMini] Step 3: Synthesizing speech...");
                await synthesizeSpeech(chatResult.text);
            } else {
                console.warn("[useMini] No chat response text, skipping synthesis");
            }
            
            console.log("[useMini] processAudioToResponse pipeline completed successfully");
            return chatResult;
        } catch (error) {
            console.error("[useMini] Error in processAudioToResponse pipeline:", error);
            throw error;
        }
    }, [transcribeAudio, sendMessage, synthesizeSpeech]);

    return {
        transcribeAudio,
        sendMessage,
        synthesizeSpeech,
        clearConversation,
        processAudioToResponse,
        isProcessing,
    };
}

