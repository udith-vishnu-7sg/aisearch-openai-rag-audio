import { useState } from "react";
import { Mic, MicOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { GroundingFiles } from "@/components/ui/grounding-files";
import GroundingFileView from "@/components/ui/grounding-file-view";
import StatusMessage from "@/components/ui/status-message";

import useRealtime from "@/hooks/useRealtime";
import useAudioRecorder from "@/hooks/useAudioRecorder";
import useAudioPlayer from "@/hooks/useAudioPlayer";

import { GroundingFile } from "./types";

import logo from "./assets/logo.svg";

function App() {
    const [isRecording, setIsRecording] = useState(false);
    const [groundingFiles, setGroundingFiles] = useState<GroundingFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<GroundingFile | null>(null);

    const { reset: resetAudioPlayer, play: playAudio, stop: stopAudioPlayer } = useAudioPlayer();
    const [isProcessing, setIsProcessing] = useState(false);
    
    const { startSession, addUserAudio, inputAudioBufferClear } = useRealtime({
        enableInputAudioTranscription: true,
        onWebSocketOpen: () => {
            console.log("[App] WebSocket connected");
            startSession();
        },
        onWebSocketClose: () => {
            console.log("[App] WebSocket disconnected");
            setIsProcessing(false);
        },
        onWebSocketError: (event) => {
            console.error("[App] WebSocket error:", event);
            setIsProcessing(false);
        },
        onReceivedResponseAudioDelta: (message) => {
            // Play audio chunks as they arrive (delta is base64 PCM audio)
            if (message.delta) {
                console.log("[App] Received audio delta, playing...");
                playAudio(message.delta);
            }
        },
        onReceivedResponseDone: () => {
            console.log("[App] Response complete");
            setIsProcessing(false);
        },
        onReceivedExtensionMiddleTierToolResponse: (message) => {
            // Handle tool results for grounding files
            if (message.tool_name === "report_grounding" && message.tool_result) {
                try {
                    const result = JSON.parse(message.tool_result);
                    if (result.sources && Array.isArray(result.sources)) {
                        const files: GroundingFile[] = result.sources.map((x: any) => ({
                            id: x.chunk_id,
                            name: x.title,
                            content: x.chunk,
                        }));
                        setGroundingFiles(prev => [...prev, ...files]);
                    }
                } catch (e) {
                    console.error("[App] Error parsing tool result:", e);
                }
            }
        },
        onReceivedInputAudioBufferSpeechStarted: () => {
            console.log("[App] Speech detected, processing...");
            setIsProcessing(true);
        },
        onReceivedError: (message) => {
            console.error("[App] Error from Realtime API:", message);
            setIsProcessing(false);
        },
    });

    const { start: startAudioRecording, stop: stopAudioRecording } = useAudioRecorder({
        onAudioRecorded: (base64Audio) => {
            // Stream audio chunks to the WebSocket
            console.log("[App] Sending audio chunk to WebSocket");
            addUserAudio(base64Audio);
        },
    });

    const onToggleListening = async () => {
        if (!isRecording) {
            console.log("[App] Starting recording session");
            resetAudioPlayer();
            setGroundingFiles([]); // Clear grounding files for new session
            inputAudioBufferClear(); // Clear any previous audio
            await startAudioRecording();
            setIsRecording(true);
            console.log("[App] Recording session started");
        } else {
            console.log("[App] Stopping recording session");
            await stopAudioRecording();
            stopAudioPlayer();
            setIsRecording(false);
            console.log("[App] Recording session stopped");
        }
    };

    const { t } = useTranslation();

    return (
        <div className="flex min-h-screen flex-col bg-gray-100 text-gray-900">
            <div className="p-4 sm:absolute sm:left-4 sm:top-4">
                <img src={logo} alt="Azure logo" className="h-16 w-16" />
            </div>
            <main className="flex flex-grow flex-col items-center justify-center">
                <h1 className="mb-8 bg-gradient-to-r from-purple-600 to-pink-600 bg-clip-text text-4xl font-bold text-transparent md:text-7xl">
                    {t("app.title")}
                </h1>
                <div className="mb-4 flex flex-col items-center justify-center">
                    <Button
                        onClick={onToggleListening}
                        className={`h-12 w-60 ${isRecording ? "bg-red-600 hover:bg-red-700" : "bg-purple-500 hover:bg-purple-600"}`}
                        aria-label={isRecording ? t("app.stopRecording") : t("app.startRecording")}
                    >
                        {isRecording ? (
                            <>
                                <MicOff className="mr-2 h-4 w-4" />
                                {t("app.stopConversation")}
                            </>
                        ) : (
                            <>
                                <Mic className="mr-2 h-6 w-6" />
                            </>
                        )}
                    </Button>
                    <StatusMessage isRecording={isRecording || isProcessing} />
                </div>
                <GroundingFiles files={groundingFiles} onSelected={setSelectedFile} />
            </main>

            <footer className="py-4 text-center">
                <p>{t("app.footer")}</p>
            </footer>

            <GroundingFileView groundingFile={selectedFile} onClosed={() => setSelectedFile(null)} />
        </div>
    );
}

export default App;
