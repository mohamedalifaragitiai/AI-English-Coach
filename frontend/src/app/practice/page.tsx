'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Volume2, Square, RotateCcw, MessageSquare } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';
const DEMO_USER_ID = 'demo_user';

type SessionState = 'disconnected' | 'connecting' | 'ready' | 'listening' | 'processing' | 'speaking';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  timestamp: Date;
}

interface Metrics {
  total_time: number;
  stt_time: number;
  llm_time: number;
  tts_time: number;
}

export default function PracticePage() {
  const [state, setState] = useState<SessionState>('disconnected');
  const [isRecording, setIsRecording] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState('');
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setState('connecting');
    setError(null);

    const ws = new WebSocket(`${WS_URL}/ws/conversation/${DEMO_USER_ID}?mode=free&level=2`);

    ws.onopen = () => {
      console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(data);
      } catch (err) {
        console.error('Failed to parse message:', err);
      }
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      setError('Connection error. Is the backend running?');
    };

    ws.onclose = () => {
      setState('disconnected');
      setIsRecording(false);
      wsRef.current = null;
    };

    wsRef.current = ws;
  }, []);

  const disconnect = useCallback(() => {
    stopRecording();
    wsRef.current?.close();
    wsRef.current = null;
    setState('disconnected');
  }, []);

  const handleMessage = (data: Record<string, unknown>) => {
    switch (data.type) {
      case 'state':
        setState(data.state as SessionState);
        break;

      case 'transcript':
        setCurrentTranscript(data.text as string);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'user',
            text: data.text as string,
            timestamp: new Date(),
          },
        ]);
        break;

      case 'response':
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            text: data.text as string,
            timestamp: new Date(),
          },
        ]);
        break;

      case 'audio':
        playAudio(data.data as string, data.sample_rate as number);
        break;

      case 'metrics':
        setMetrics(data as unknown as Metrics);
        break;

      case 'error':
        setError(data.error as string);
        break;
    }
  };

  const playAudio = async (base64Audio: string, sampleRate: number) => {
    try {
      const audioData = atob(base64Audio);
      const arrayBuffer = new ArrayBuffer(audioData.length);
      const view = new Uint8Array(arrayBuffer);
      for (let i = 0; i < audioData.length; i++) {
        view[i] = audioData.charCodeAt(i);
      }

      if (!audioContextRef.current) {
        audioContextRef.current = new AudioContext();
      }

      const audioBuffer = await audioContextRef.current.decodeAudioData(arrayBuffer);
      const source = audioContextRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContextRef.current.destination);
      source.start();
    } catch (err) {
      console.error('Failed to play audio:', err);
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      const audioContext = new AudioContext({ sampleRate: 16000 });
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);

      source.connect(processor);
      processor.connect(audioContext.destination);

      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const int16Data = new Int16Array(inputData.length);
          for (let i = 0; i < inputData.length; i++) {
            int16Data[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
          }
          wsRef.current.send(int16Data.buffer);
        }
      };

      audioContextRef.current = audioContext;
      setIsRecording(true);
    } catch (err) {
      console.error('Failed to start recording:', err);
      setError('Failed to access microphone');
    }
  };

  const stopRecording = () => {
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setIsRecording(false);
  };

  const toggleRecording = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  const resetConversation = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'reset' }));
      setMessages([]);
      setCurrentTranscript('');
      setMetrics(null);
    }
  };

  const getStateColor = () => {
    switch (state) {
      case 'ready':
        return 'bg-green-500';
      case 'listening':
        return 'bg-blue-500';
      case 'processing':
        return 'bg-yellow-500';
      case 'speaking':
        return 'bg-purple-500';
      default:
        return 'bg-slate-400';
    }
  };

  return (
    <DashboardLayout userName="Demo User" streakDays={5} level="A2">
      <div className="mx-auto max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Practice Session</h1>
            <p className="text-slate-500">Talk with your AI English coach</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={cn('h-3 w-3 rounded-full', getStateColor())} />
            <Badge variant="outline" className="capitalize">
              {state}
            </Badge>
          </div>
        </div>

        {/* Connection Controls */}
        {state === 'disconnected' ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-12">
              <MessageSquare className="h-12 w-12 text-slate-300 mb-4" />
              <p className="text-slate-500 mb-4">Connect to start practicing</p>
              <Button onClick={connect} size="lg">
                Start Session
              </Button>
              {error && (
                <p className="mt-4 text-sm text-red-500">{error}</p>
              )}
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Conversation */}
            <Card className="h-[400px]">
              <CardHeader className="pb-2">
                <CardTitle className="text-lg flex items-center justify-between">
                  Conversation
                  <Button variant="ghost" size="sm" onClick={resetConversation}>
                    <RotateCcw className="h-4 w-4 mr-1" />
                    Reset
                  </Button>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[300px] pr-4">
                  <div className="space-y-4">
                    {messages.length === 0 ? (
                      <p className="text-center text-slate-400 py-8">
                        Start speaking to begin the conversation...
                      </p>
                    ) : (
                      messages.map((msg) => (
                        <div
                          key={msg.id}
                          className={cn(
                            'flex',
                            msg.role === 'user' ? 'justify-end' : 'justify-start'
                          )}
                        >
                          <div
                            className={cn(
                              'rounded-lg px-4 py-2 max-w-[80%]',
                              msg.role === 'user'
                                ? 'bg-blue-500 text-white'
                                : 'bg-slate-100 text-slate-900'
                            )}
                          >
                            <p>{msg.text}</p>
                            <p className="text-xs opacity-70 mt-1">
                              {msg.timestamp.toLocaleTimeString()}
                            </p>
                          </div>
                        </div>
                      ))
                    )}
                    <div ref={messagesEndRef} />
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>

            {/* Controls */}
            <Card>
              <CardContent className="py-6">
                <div className="flex items-center justify-center gap-4">
                  <Button
                    size="lg"
                    variant={isRecording ? 'destructive' : 'default'}
                    onClick={toggleRecording}
                    disabled={state === 'processing' || state === 'speaking'}
                    className="h-16 w-16 rounded-full"
                  >
                    {isRecording ? (
                      <MicOff className="h-6 w-6" />
                    ) : (
                      <Mic className="h-6 w-6" />
                    )}
                  </Button>
                  <Button
                    size="lg"
                    variant="outline"
                    onClick={disconnect}
                    className="h-16 w-16 rounded-full"
                  >
                    <Square className="h-6 w-6" />
                  </Button>
                </div>
                <p className="text-center text-sm text-slate-500 mt-4">
                  {isRecording
                    ? 'Listening... Speak clearly into your microphone'
                    : state === 'processing'
                    ? 'Processing your speech...'
                    : state === 'speaking'
                    ? 'Coach is responding...'
                    : 'Click the microphone to start speaking'}
                </p>
              </CardContent>
            </Card>

            {/* Metrics */}
            {metrics && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-slate-500">Turn Metrics</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex justify-around text-center">
                    <div>
                      <p className="text-lg font-bold">{metrics.stt_time.toFixed(2)}s</p>
                      <p className="text-xs text-slate-500">STT</p>
                    </div>
                    <div>
                      <p className="text-lg font-bold">{metrics.llm_time.toFixed(2)}s</p>
                      <p className="text-xs text-slate-500">LLM</p>
                    </div>
                    <div>
                      <p className="text-lg font-bold">{metrics.tts_time.toFixed(2)}s</p>
                      <p className="text-xs text-slate-500">TTS</p>
                    </div>
                    <div>
                      <p className={cn(
                        'text-lg font-bold',
                        metrics.total_time < 2 ? 'text-green-600' : 'text-orange-600'
                      )}>
                        {metrics.total_time.toFixed(2)}s
                      </p>
                      <p className="text-xs text-slate-500">Total</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </DashboardLayout>
  );
}
