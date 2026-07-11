'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Square, RotateCcw, Phone, PhoneOff, AlertCircle, Volume2, Send } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';

type SessionState = 'idle' | 'recording' | 'processing' | 'playing';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
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
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState<SessionState>('idle');
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [textInput, setTextInput] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const animationRef = useRef<number | null>(null);
  const audioChunksRef = useRef<Int16Array[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    return () => {
      stopRecording();
      wsRef.current?.close();
    };
  }, []);

  const updateLevel = () => {
    if (analyserRef.current) {
      const data = new Uint8Array(analyserRef.current.frequencyBinCount);
      analyserRef.current.getByteFrequencyData(data);
      setAudioLevel(data.reduce((a, b) => a + b) / data.length / 255);
      animationRef.current = requestAnimationFrame(updateLevel);
    }
  };

  const connect = () => {
    const ws = new WebSocket(`${WS_URL}/ws/conversation/user1?mode=free&level=${selectedLevel}`);

    ws.onopen = () => {
      setConnected(true);
      setError(null);
    };

    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);

      if (data.type === 'transcript') {
        setMessages(m => [...m, { id: Date.now().toString(), role: 'user', text: data.text }]);
      } else if (data.type === 'response') {
        setMessages(m => [...m, { id: Date.now().toString(), role: 'assistant', text: data.text }]);
      } else if (data.type === 'audio') {
        playAudio(data.data);
      } else if (data.type === 'error') {
        setError(data.error);
        setState('idle');
      } else if (data.type === 'state' && data.state === 'ready') {
        if (state === 'processing' || state === 'playing') {
          setState('idle');
        }
      }
    };

    ws.onerror = () => setError('Connection failed');
    ws.onclose = () => { setConnected(false); setState('idle'); };
    wsRef.current = ws;
  };

  const playAudio = async (base64: string) => {
    setState('playing');
    try {
      const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: 'audio/wav' });
      const audio = new Audio(URL.createObjectURL(blob));
      audio.onended = () => setState('idle');
      await audio.play();
    } catch (e) {
      console.error(e);
      setState('idle');
    }
  };

  // START RECORDING - user clicks green button
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000 } });
      streamRef.current = stream;
      audioChunksRef.current = [];

      const ctx = new AudioContext({ sampleRate: 16000 });
      const source = ctx.createMediaStreamSource(stream);

      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      const processor = ctx.createScriptProcessor(4096, 1, 1);
      source.connect(processor);
      processor.connect(ctx.destination);
      processor.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, input[i] * 32768));
        }
        audioChunksRef.current.push(int16);
      };

      processorRef.current = processor;
      audioContextRef.current = ctx;
      setState('recording');
      updateLevel();
    } catch (e) {
      setError('Microphone access denied');
    }
  };

  // STOP RECORDING - user clicks red stop button
  const stopRecording = () => {
    if (animationRef.current) cancelAnimationFrame(animationRef.current);
    if (processorRef.current) processorRef.current.disconnect();
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
    if (audioContextRef.current) audioContextRef.current.close().catch(() => {});

    analyserRef.current = null;
    setAudioLevel(0);

    // Send audio
    if (wsRef.current?.readyState === WebSocket.OPEN && audioChunksRef.current.length > 0) {
      const total = audioChunksRef.current.reduce((s, c) => s + c.length, 0);
      const combined = new Int16Array(total);
      let offset = 0;
      for (const chunk of audioChunksRef.current) {
        combined.set(chunk, offset);
        offset += chunk.length;
      }
      wsRef.current.send(combined.buffer);
      setState('processing');
    } else {
      setState('idle');
    }
    audioChunksRef.current = [];
  };

  const sendText = () => {
    if (!textInput.trim() || !wsRef.current || state !== 'idle') return;
    wsRef.current.send(JSON.stringify({ type: 'text', text: textInput }));
    setMessages(m => [...m, { id: Date.now().toString(), role: 'user', text: textInput }]);
    setTextInput('');
    setState('processing');
  };

  // NOT CONNECTED - Level selection
  if (!connected) {
    return (
      <DashboardLayout userName="Learner" streakDays={0} level="--">
        <div className="mx-auto max-w-xl">
          <Card>
            <CardHeader className="text-center">
              <CardTitle>Select Your Level</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 mb-6">
                {LEVELS.map((l) => (
                  <button
                    key={l.id}
                    onClick={() => setSelectedLevel(l.id)}
                    className={cn(
                      'p-3 rounded-lg border-2 transition-all',
                      selectedLevel === l.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200'
                    )}
                  >
                    <div className={cn('w-10 h-10 mx-auto rounded-lg flex items-center justify-center text-white font-bold mb-1', l.color)}>
                      {l.code}
                    </div>
                    <div className="text-xs">{l.name}</div>
                  </button>
                ))}
              </div>
              <Button onClick={connect} className="w-full h-12" size="lg">
                <Phone className="w-5 h-5 mr-2" /> Start Practice
              </Button>
              {error && <p className="mt-3 text-red-500 text-sm text-center">{error}</p>}
            </CardContent>
          </Card>
        </div>
      </DashboardLayout>
    );
  }

  // CONNECTED - Chat UI
  return (
    <DashboardLayout userName="Learner" streakDays={0} level={LEVELS[selectedLevel].code}>
      <div className="mx-auto max-w-2xl h-[calc(100vh-7rem)] flex flex-col">
        {/* Header */}
        <div className="flex justify-between items-center mb-2">
          <span className="font-semibold">{LEVELS[selectedLevel].name} Practice</span>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => { setMessages([]); wsRef.current?.send(JSON.stringify({type:'reset'})); }}>
              <RotateCcw className="w-4 h-4" />
            </Button>
            <Button size="sm" variant="outline" onClick={() => { wsRef.current?.close(); setConnected(false); }} className="text-red-500">
              <PhoneOff className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Messages */}
        <Card className="flex-1 overflow-hidden mb-3">
          <div className="h-full overflow-y-auto p-3 space-y-3">
            {messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-400">
                <Mic className="w-12 h-12 mb-2" />
                <p>Press the green button to start speaking</p>
              </div>
            ) : (
              messages.map((m) => (
                <div key={m.id} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                  <div className={cn(
                    'max-w-[80%] rounded-2xl px-4 py-2',
                    m.role === 'user' ? 'bg-blue-500 text-white' : 'bg-gray-100'
                  )}>
                    {m.text}
                  </div>
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>
        </Card>

        {/* Audio Level */}
        {state === 'recording' && (
          <div className="flex justify-center gap-1 h-8 mb-2">
            {Array.from({length: 15}).map((_, i) => (
              <div
                key={i}
                className={cn('w-2 rounded transition-all', i/15 < audioLevel ? 'bg-green-500' : 'bg-gray-200')}
                style={{ height: `${10 + (i/15 < audioLevel ? audioLevel * 20 : 0)}px` }}
              />
            ))}
          </div>
        )}

        {/* BIG BUTTON */}
        <div className="flex flex-col items-center mb-3">
          {state === 'idle' && (
            <button
              onClick={startRecording}
              className="w-24 h-24 rounded-full bg-green-500 hover:bg-green-600 text-white flex items-center justify-center shadow-lg transition-transform hover:scale-105"
            >
              <Mic className="w-10 h-10" />
            </button>
          )}

          {state === 'recording' && (
            <button
              onClick={stopRecording}
              className="w-24 h-24 rounded-full bg-red-500 hover:bg-red-600 text-white flex items-center justify-center shadow-lg animate-pulse"
            >
              <Square className="w-10 h-10" />
            </button>
          )}

          {state === 'processing' && (
            <div className="w-24 h-24 rounded-full bg-yellow-500 text-white flex items-center justify-center shadow-lg">
              <div className="w-10 h-10 border-4 border-white border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {state === 'playing' && (
            <div className="w-24 h-24 rounded-full bg-purple-500 text-white flex items-center justify-center shadow-lg">
              <Volume2 className="w-10 h-10 animate-pulse" />
            </div>
          )}

          <p className="mt-2 text-sm text-gray-500">
            {state === 'idle' && 'Tap to speak'}
            {state === 'recording' && 'Recording... Tap to stop'}
            {state === 'processing' && 'Processing...'}
            {state === 'playing' && 'Playing response...'}
          </p>
        </div>

        {/* Text input */}
        <div className="flex gap-2">
          <input
            type="text"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && sendText()}
            placeholder="Or type here..."
            disabled={state !== 'idle'}
            className="flex-1 px-3 py-2 border rounded-lg disabled:bg-gray-100"
          />
          <Button onClick={sendText} disabled={state !== 'idle' || !textInput.trim()}>
            <Send className="w-4 h-4" />
          </Button>
        </div>

        {error && <p className="mt-2 text-red-500 text-sm text-center">{error}</p>}
      </div>
    </DashboardLayout>
  );
}
