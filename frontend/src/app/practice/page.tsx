'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Mic, MicOff, Volume2, RotateCcw, MessageSquare,
  ChevronRight, Sparkles, Zap, Clock, CheckCircle2, AlertCircle,
  Phone, PhoneOff, Send
} from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';

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

const LEVELS = [
  { id: 0, code: 'A1', name: 'Beginner', color: 'bg-emerald-500' },
  { id: 1, code: 'A2', name: 'Elementary', color: 'bg-green-500' },
  { id: 2, code: 'B1', name: 'Intermediate', color: 'bg-blue-500' },
  { id: 3, code: 'B2', name: 'Upper Intermediate', color: 'bg-indigo-500' },
  { id: 4, code: 'C1', name: 'Advanced', color: 'bg-purple-500' },
  { id: 5, code: 'C2', name: 'Proficient', color: 'bg-pink-500' },
];

export default function PracticePage() {
  const [selectedLevel, setSelectedLevel] = useState<number>(2);
  const [state, setState] = useState<SessionState>('disconnected');
  const [isRecording, setIsRecording] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingResponse, setStreamingResponse] = useState('');
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [textInput, setTextInput] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const audioChunksRef = useRef<Int16Array[]>([]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingResponse]);

  useEffect(() => {
    return () => {
      cleanup();
    };
  }, []);

  const cleanup = () => {
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
    if (audioContextRef.current) audioContextRef.current.close().catch(() => {});
    if (wsRef.current) wsRef.current.close();
  };

  const updateAudioLevel = useCallback(() => {
    if (analyserRef.current && isRecording) {
      const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
      analyserRef.current.getByteFrequencyData(dataArray);
      const avg = dataArray.reduce((a, b) => a + b) / dataArray.length;
      setAudioLevel(avg / 255);
      animationFrameRef.current = requestAnimationFrame(updateAudioLevel);
    }
  }, [isRecording]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setState('connecting');
    setError(null);

    const ws = new WebSocket(`${WS_URL}/ws/conversation/user1?mode=free&level=${selectedLevel}`);

    ws.onopen = () => {
      console.log('Connected');
      setState('ready');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(data);
      } catch (err) {
        console.error('Parse error:', err);
      }
    };

    ws.onerror = () => {
      setError('Connection failed. Is backend running on port 8001?');
      setState('disconnected');
    };

    ws.onclose = () => {
      setState('disconnected');
      setIsRecording(false);
    };

    wsRef.current = ws;
  }, [selectedLevel]);

  const disconnect = useCallback(() => {
    cleanup();
    wsRef.current = null;
    setState('disconnected');
    setMessages([]);
  }, []);

  const handleMessage = (data: Record<string, unknown>) => {
    console.log('Received:', data.type);

    switch (data.type) {
      case 'state':
        const newState = data.state as SessionState;
        setState(newState);
        break;

      case 'transcript':
        setMessages(prev => [...prev, {
          id: crypto.randomUUID(),
          role: 'user',
          text: data.text as string,
          timestamp: new Date(),
        }]);
        setState('processing');
        break;

      case 'response_chunk':
        setStreamingResponse(prev => prev + (data.text as string));
        break;

      case 'response':
        setStreamingResponse('');
        setMessages(prev => [...prev, {
          id: crypto.randomUUID(),
          role: 'assistant',
          text: data.text as string,
          timestamp: new Date(),
        }]);
        break;

      case 'audio':
        console.log('Audio received, playing...');
        playAudio(data.data as string);
        break;

      case 'metrics':
        setMetrics(data as unknown as Metrics);
        setState('ready');
        break;

      case 'error':
        setError(data.error as string);
        setState('ready');
        break;
    }
  };

  const playAudio = async (base64Audio: string) => {
    try {
      const byteChars = atob(base64Audio);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }

      const blob = new Blob([byteNumbers], { type: 'audio/wav' });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);

      audio.onended = () => {
        URL.revokeObjectURL(url);
        setState('ready');
      };

      setState('speaking');
      await audio.play();
    } catch (err) {
      console.error('Audio play error:', err);
      setState('ready');
    }
  };

  // START recording - user presses button
  const startRecording = async () => {
    if (state !== 'ready') return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
      });
      streamRef.current = stream;
      audioChunksRef.current = [];

      const audioContext = new AudioContext({ sampleRate: 16000 });
      const source = audioContext.createMediaStreamSource(stream);

      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      source.connect(processor);
      processor.connect(audioContext.destination);

      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
        }
        audioChunksRef.current.push(int16);
      };

      processorRef.current = processor;
      audioContextRef.current = audioContext;

      setIsRecording(true);
      setState('listening');
      updateAudioLevel();

    } catch (err) {
      console.error('Mic error:', err);
      setError('Microphone access denied');
    }
  };

  // STOP recording - user releases button, send all audio
  const stopRecording = async () => {
    if (!isRecording) return;

    // Stop audio capture
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    if (processorRef.current) processorRef.current.disconnect();
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
    if (audioContextRef.current) {
      await audioContextRef.current.close().catch(() => {});
    }

    setIsRecording(false);
    setAudioLevel(0);
    setState('processing');

    // Send all collected audio
    if (wsRef.current?.readyState === WebSocket.OPEN && audioChunksRef.current.length > 0) {
      const totalLength = audioChunksRef.current.reduce((sum, chunk) => sum + chunk.length, 0);
      const combined = new Int16Array(totalLength);
      let offset = 0;
      for (const chunk of audioChunksRef.current) {
        combined.set(chunk, offset);
        offset += chunk.length;
      }

      console.log(`Sending ${combined.length} samples (${(combined.length / 16000).toFixed(1)}s of audio)`);
      wsRef.current.send(combined.buffer);
    }

    audioChunksRef.current = [];
  };

  // Send text message directly
  const sendTextMessage = () => {
    if (!textInput.trim() || !wsRef.current || state !== 'ready') return;

    wsRef.current.send(JSON.stringify({ type: 'text', text: textInput.trim() }));
    setMessages(prev => [...prev, {
      id: crypto.randomUUID(),
      role: 'user',
      text: textInput.trim(),
      timestamp: new Date(),
    }]);
    setTextInput('');
    setState('processing');
  };

  const resetConversation = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'reset' }));
      setMessages([]);
      setStreamingResponse('');
      setMetrics(null);
      setState('ready');
    }
  };

  // Button colors based on state
  const getMicButtonStyle = () => {
    if (isRecording) {
      return 'bg-green-500 hover:bg-green-600 text-white animate-pulse';
    }
    if (state === 'processing' || state === 'speaking') {
      return 'bg-red-500 text-white cursor-not-allowed';
    }
    return 'bg-blue-600 hover:bg-blue-700 text-white';
  };

  // Not connected - show level selection
  if (state === 'disconnected' || state === 'connecting') {
    return (
      <DashboardLayout userName="Learner" streakDays={0} level="--">
        <div className="mx-auto max-w-2xl">
          <Card className="shadow-xl">
            <CardHeader className="text-center">
              <CardTitle className="text-2xl">Select Your Level</CardTitle>
              <CardDescription>Choose your English proficiency level</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-3 mb-6">
                {LEVELS.map((level) => (
                  <button
                    key={level.id}
                    onClick={() => setSelectedLevel(level.id)}
                    className={cn(
                      'p-4 rounded-xl border-2 transition-all',
                      selectedLevel === level.id
                        ? 'border-blue-500 bg-blue-50 scale-105'
                        : 'border-slate-200 hover:border-slate-300'
                    )}
                  >
                    <div className={cn('w-12 h-12 mx-auto rounded-lg flex items-center justify-center text-white font-bold mb-2', level.color)}>
                      {level.code}
                    </div>
                    <div className="text-sm font-medium">{level.name}</div>
                  </button>
                ))}
              </div>

              <Button
                onClick={connect}
                disabled={state === 'connecting'}
                className="w-full h-14 text-lg bg-gradient-to-r from-blue-600 to-indigo-600"
                size="lg"
              >
                {state === 'connecting' ? (
                  <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />Connecting...</>
                ) : (
                  <><Phone className="w-6 h-6 mr-2" />Start Practice</>
                )}
              </Button>

              {error && (
                <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-600 text-sm flex items-center gap-2">
                  <AlertCircle className="w-4 h-4" />{error}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </DashboardLayout>
    );
  }

  // Connected - show conversation
  return (
    <DashboardLayout userName="Learner" streakDays={0} level={LEVELS[selectedLevel].code}>
      <div className="mx-auto max-w-3xl h-[calc(100vh-8rem)] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center text-white font-bold', LEVELS[selectedLevel].color)}>
              {LEVELS[selectedLevel].code}
            </div>
            <div>
              <h1 className="font-semibold">Practice Session</h1>
              <div className="flex items-center gap-2 text-sm">
                <span className={cn(
                  'w-2 h-2 rounded-full',
                  state === 'ready' ? 'bg-green-500' :
                  state === 'listening' ? 'bg-green-500 animate-pulse' :
                  state === 'processing' ? 'bg-yellow-500 animate-pulse' :
                  state === 'speaking' ? 'bg-purple-500 animate-pulse' : 'bg-slate-400'
                )} />
                <span className="text-slate-500 capitalize">{state}</span>
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={resetConversation}>
              <RotateCcw className="w-4 h-4 mr-1" />Reset
            </Button>
            <Button variant="outline" size="sm" onClick={disconnect} className="text-red-600">
              <PhoneOff className="w-4 h-4 mr-1" />End
            </Button>
          </div>
        </div>

        {/* Messages */}
        <Card className="flex-1 overflow-hidden mb-3">
          <div className="h-full overflow-y-auto p-4">
            {messages.length === 0 && !streamingResponse ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <Mic className="w-16 h-16 text-slate-300 mb-4" />
                <h3 className="font-semibold text-lg mb-2">Ready to Practice!</h3>
                <p className="text-slate-500 max-w-sm">
                  Hold the <span className="text-green-600 font-medium">green microphone button</span> while speaking,
                  then release to send your message.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {messages.map((msg) => (
                  <div key={msg.id} className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}>
                    <div className={cn(
                      'rounded-2xl px-4 py-3 max-w-[80%]',
                      msg.role === 'user'
                        ? 'bg-blue-600 text-white rounded-br-sm'
                        : 'bg-slate-100 text-slate-800 rounded-bl-sm'
                    )}>
                      <p>{msg.text}</p>
                    </div>
                  </div>
                ))}
                {streamingResponse && (
                  <div className="flex justify-start">
                    <div className="rounded-2xl rounded-bl-sm px-4 py-3 max-w-[80%] bg-slate-100">
                      <p>{streamingResponse}</p>
                      <span className="inline-flex gap-1 mt-1">
                        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" />
                        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'0.1s'}} />
                        <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{animationDelay:'0.2s'}} />
                      </span>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>
        </Card>

        {/* Controls */}
        <div className="bg-white rounded-2xl border p-4 shadow-sm">
          {/* Audio level bars */}
          {isRecording && (
            <div className="flex items-center justify-center gap-1 h-8 mb-3">
              {Array.from({ length: 20 }).map((_, i) => (
                <div
                  key={i}
                  className={cn(
                    'w-1.5 rounded-full transition-all duration-75',
                    i / 20 < audioLevel ? 'bg-green-500' : 'bg-slate-200'
                  )}
                  style={{ height: `${8 + (i / 20 < audioLevel ? audioLevel * 24 : 0)}px` }}
                />
              ))}
            </div>
          )}

          {/* Main controls */}
          <div className="flex items-center justify-center gap-4">
            {/* Big microphone button - PUSH TO TALK */}
            <button
              onMouseDown={startRecording}
              onMouseUp={stopRecording}
              onMouseLeave={isRecording ? stopRecording : undefined}
              onTouchStart={startRecording}
              onTouchEnd={stopRecording}
              disabled={state === 'processing' || state === 'speaking'}
              className={cn(
                'w-20 h-20 rounded-full flex items-center justify-center transition-all shadow-lg',
                'focus:outline-none focus:ring-4 focus:ring-offset-2',
                getMicButtonStyle(),
                isRecording && 'scale-110 ring-4 ring-green-300'
              )}
            >
              {state === 'processing' ? (
                <div className="w-8 h-8 border-4 border-white border-t-transparent rounded-full animate-spin" />
              ) : state === 'speaking' ? (
                <Volume2 className="w-8 h-8 animate-pulse" />
              ) : (
                <Mic className="w-8 h-8" />
              )}
            </button>
          </div>

          {/* Status text */}
          <p className="text-center text-sm mt-3 font-medium">
            {isRecording ? (
              <span className="text-green-600">Recording... Release to send</span>
            ) : state === 'processing' ? (
              <span className="text-yellow-600">Processing your message...</span>
            ) : state === 'speaking' ? (
              <span className="text-purple-600">Coach is speaking...</span>
            ) : (
              <span className="text-slate-500">Hold to speak</span>
            )}
          </p>

          {/* Text input fallback */}
          <div className="flex gap-2 mt-4 pt-4 border-t">
            <input
              type="text"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendTextMessage()}
              placeholder="Or type your message..."
              disabled={state !== 'ready'}
              className="flex-1 px-4 py-2 rounded-lg border focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-100"
            />
            <Button onClick={sendTextMessage} disabled={state !== 'ready' || !textInput.trim()}>
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Metrics */}
        {metrics && (
          <div className="mt-2 flex justify-center gap-4 text-xs text-slate-500">
            <span>STT: {metrics.stt_time.toFixed(2)}s</span>
            <span>LLM: {metrics.llm_time.toFixed(2)}s</span>
            <span>TTS: {metrics.tts_time.toFixed(2)}s</span>
            <span className={metrics.total_time < 3 ? 'text-green-600' : 'text-amber-600'}>
              Total: {metrics.total_time.toFixed(2)}s
            </span>
          </div>
        )}

        {error && (
          <div className="mt-2 p-3 rounded-lg bg-red-50 text-red-600 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />{error}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
