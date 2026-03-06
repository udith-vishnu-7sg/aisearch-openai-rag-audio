import { useRef } from "react";
import { Recorder } from "@/components/audio/recorder";

type Parameters = {
    onRecordingComplete: (audioBase64: string) => void;
};

export default function useAudioRecorderMini({ onRecordingComplete }: Parameters) {
    const audioRecorder = useRef<Recorder>();
    const audioBuffer = useRef<Uint8Array>(new Uint8Array());

    const handleAudioData = (data: Iterable<number>) => {
        const uint8Array = new Uint8Array(data);
        // Accumulate all audio data
        const newBuffer = new Uint8Array(audioBuffer.current.length + uint8Array.length);
        newBuffer.set(audioBuffer.current);
        newBuffer.set(uint8Array, audioBuffer.current.length);
        audioBuffer.current = newBuffer;
        // Log periodically (every ~1MB to avoid spam)
        if (audioBuffer.current.length % 1048576 < uint8Array.length) {
            console.log("[useAudioRecorderMini] Audio buffer size:", audioBuffer.current.length, "bytes");
        }
    };

    const start = async () => {
        console.log("[useAudioRecorderMini] Starting recording...");
        try {
            // Reset buffer
            audioBuffer.current = new Uint8Array();
            
            if (!audioRecorder.current) {
                console.log("[useAudioRecorderMini] Creating new Recorder instance");
                audioRecorder.current = new Recorder(handleAudioData);
            }
            console.log("[useAudioRecorderMini] Requesting microphone access...");
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            console.log("[useAudioRecorderMini] Microphone access granted, starting recorder");
            audioRecorder.current.start(stream);
            console.log("[useAudioRecorderMini] Recording started successfully");
        } catch (error) {
            console.error("[useAudioRecorderMini] Error starting recording:", error);
            throw error;
        }
    };

    const stop = async () => {
        console.log("[useAudioRecorderMini] Stopping recording...");
        try {
            await audioRecorder.current?.stop();
            console.log("[useAudioRecorderMini] Recorder stopped, buffer size:", audioBuffer.current.length);
            
            // Convert accumulated audio to base64
            if (audioBuffer.current.length > 0) {
                console.log("[useAudioRecorderMini] Converting audio buffer to base64...");
                // Convert Uint8Array to base64 using FileReader (avoids stack overflow from spread operator)
                await new Promise<void>((resolve, reject) => {
                    const blob = new Blob([audioBuffer.current], { type: 'application/octet-stream' });
                    const reader = new FileReader();
                    reader.onloadend = () => {
                        try {
                            // FileReader.result is a data URL like "data:application/octet-stream;base64,..."
                            const dataUrl = reader.result as string;
                            const base64 = dataUrl.split(',')[1]; // Extract base64 part
                            console.log("[useAudioRecorderMini] Audio converted to base64, length:", base64.length);
                            onRecordingComplete(base64);
                            resolve();
                        } catch (error) {
                            console.error("[useAudioRecorderMini] Error converting to base64:", error);
                            reject(error);
                        }
                    };
                    reader.onerror = (error) => {
                        console.error("[useAudioRecorderMini] FileReader error:", error);
                        reject(error);
                    };
                    reader.readAsDataURL(blob);
                });
            } else {
                console.warn("[useAudioRecorderMini] Audio buffer is empty, skipping conversion");
            }
            
            // Reset buffer
            audioBuffer.current = new Uint8Array();
            console.log("[useAudioRecorderMini] Recording stopped successfully");
        } catch (error) {
            console.error("[useAudioRecorderMini] Error stopping recording:", error);
            throw error;
        }
    };

    return { start, stop };
}

