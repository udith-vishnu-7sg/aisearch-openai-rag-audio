import { useRef } from "react";
import { Recorder } from "@/components/audio/recorder";

const BUFFER_SIZE = 4800;

type Parameters = {
    onAudioRecorded: (base64: string) => void;
};

export default function useAudioRecorder({ onAudioRecorded }: Parameters) {
    const audioRecorder = useRef<Recorder>();

    let buffer = new Uint8Array();

    const appendToBuffer = (newData: Uint8Array) => {
        const newBuffer = new Uint8Array(buffer.length + newData.length);
        newBuffer.set(buffer);
        newBuffer.set(newData, buffer.length);
        buffer = newBuffer;
    };

    const handleAudioData = (data: Iterable<number>) => {
        const uint8Array = new Uint8Array(data);
        appendToBuffer(uint8Array);

        if (buffer.length >= BUFFER_SIZE) {
            const toSend = new Uint8Array(buffer.slice(0, BUFFER_SIZE));
            buffer = new Uint8Array(buffer.slice(BUFFER_SIZE));

            // Convert Uint8Array to base64 using FileReader (avoids stack overflow from spread operator)
            const blob = new Blob([toSend], { type: 'application/octet-stream' });
            const reader = new FileReader();
            reader.onloadend = () => {
                try {
                    const dataUrl = reader.result as string;
                    const base64 = dataUrl.split(',')[1]; // Extract base64 part
                    onAudioRecorded(base64);
                } catch (error) {
                    console.error("[useAudioRecorder] Error converting to base64:", error);
                }
            };
            reader.onerror = (error) => {
                console.error("[useAudioRecorder] FileReader error:", error);
            };
            reader.readAsDataURL(blob);
        }
    };

    const start = async () => {
        if (!audioRecorder.current) {
            audioRecorder.current = new Recorder(handleAudioData);
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioRecorder.current.start(stream);
    };

    const stop = async () => {
        await audioRecorder.current?.stop();
    };

    return { start, stop };
}
