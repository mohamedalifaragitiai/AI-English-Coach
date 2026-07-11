'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Mic, MicOff, Volume2, Square, RotateCcw, MessageSquare,
  ChevronRight, Sparkles, Zap, Clock, CheckCircle2, AlertCircle,
  Waves, Settings2, Phone, PhoneOff
} from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';

type SessionState = 'disconnected' | 'connecting' | 'ready' | 'listening' | 'processing' | 'speaking';
type OnboardingStep = 'level' | 'mic-test' | 'ready';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  timestamp: Date;
  confidence?: number;
}

interface Metrics {
  total_time: number;
  stt_time: number;
  llm_time: number;
  tts_time: number;
}

const LEVELS = [
  { id: 0, code: 'A1', name: 'Beginner', description: 'Basic phrases and simple sentences', color: 'bg-emerald-500' },
  { id: 1, code: 'A2', name: 'Elementary', description: 'Everyday expressions and basic conversation', color: 'bg-green-500' },
  { id: 2, code: 'B1', name: 'Intermediate', description: 'Clear speech on familiar topics', color: 'bg-blue-500' },
  { id: 3, code: 'B2', name: 'Upper Intermediate', description: 'Complex topics and spontaneous interaction', color: 'bg-indigo-500' },
  { id: 4, code: 'C1', name: 'Advanced', description: 'Fluent and flexible language use', color: 'bg-purple-500' },
  { id: 5, code: 'C2', name: 'Proficient', description: 'Near-native level proficiency', color: 'bg-pink-500' },
];

const CONVERSATION_MODES = [
  { id: 'free', name: 'Free Conversation', icon: MessageSquare, description: 'Open-ended practice on any topic' },
  { id: 'roleplay', name: 'Role Play', icon: Sparkles, description: 'Practice real-world scenarios' },
];

export default function PracticePage() {
  // Onboarding state
  const [onboardingStep, setOnboardingStep] = useState<OnboardingStep>('level');
  const [selectedLevel, setSelectedLevel] = useState<number | null>(null);
  const [selectedMode, setSelectedMode] = useState<string>('free');
  const [micTested, setMicTested] = useState(false);

  // Session state
  const [state, setState] = useState<SessionState>('disconnected');
  const [isRecording, setIsRecording] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState('');
  const [streamingResponse, setStreamingResponse] = useState('');
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [sessionStarted, setSessionStarted] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingResponse]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopRecording();
      wsRef.current?.close();
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  const updateAudioLevel = useCallback(() => {
    if (analyserRef.current) {
      const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
      analyserRef.current.getByteFrequencyData(dataArray);
      const average = dataArray.reduce((a, b) => a + b) / dataArray.length;
      setAudioLevel(average / 255);
    }
    animationFrameRef.current = requestAnimationFrame(updateAudioLevel);
  }, []);

  const testMicrophone = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);

      analyserRef.current = analyser;
      audioContextRef.current = audioContext;

      updateAudioLevel();
      setMicTested(true);
      setError(null);
    } catch (err) {
      console.error('Microphone access failed:', err);
      setError('Could not access microphone. Please check permissions.');
    }
  };

  const stopMicTest = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    analyserRef.current = null;
    setAudioLevel(0);
  };

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    if (selectedLevel === null) return;

    setState('connecting');
    setError(null);

    const ws = new WebSocket(`${WS_URL}/ws/conversation/demo_user?mode=${selectedMode}&level=${selectedLevel}`);

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
      setError('Connection failed. Make sure the backend is running on port 8001.');
      setState('disconnected');
    };

    ws.onclose = () => {
      setState('disconnected');
      setIsRecording(false);
      wsRef.current = null;
    };

    wsRef.current = ws;
  }, [selectedLevel, selectedMode]);

  const disconnect = useCallback(() => {
    stopRecording();
    wsRef.current?.close();
    wsRef.current = null;
    setState('disconnected');
    setSessionStarted(false);
    setMessages([]);
    setMetrics(null);
  }, []);

  const handleMessage = (data: Record<string, unknown>) => {
    switch (data.type) {
      case 'state':
        setState(data.state as SessionState);
        if (data.state === 'ready' && !sessionStarted) {
          setSessionStarted(true);
        }
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
            confidence: data.confidence as number,
          },
        ]);
        break;

      case 'response_chunk':
        setStreamingResponse(prev => prev + (data.text as string));
        break;

      case 'response':
        setStreamingResponse('');
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

      if (!playbackContextRef.current) {
        playbackContextRef.current = new AudioContext();
      }

      const audioBuffer = await playbackContextRef.current.decodeAudioData(arrayBuffer);
      const source = playbackContextRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(playbackContextRef.current.destination);
      source.start();
    } catch (err) {
      console.error('Failed to play audio:', err);
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        }
      });
      streamRef.current = stream;

      const audioContext = new AudioContext({ sampleRate: 16000 });
      const source = audioContext.createMediaStreamSource(stream);

      // Create analyser for level visualization
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // Create processor for sending audio
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
      updateAudioLevel();
      setIsRecording(true);
    } catch (err) {
      console.error('Failed to start recording:', err);
      setError('Failed to access microphone. Please check permissions.');
    }
  };

  const stopRecording = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    analyserRef.current = null;
    setAudioLevel(0);
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
      setStreamingResponse('');
      setMetrics(null);
    }
  };

  const getStateInfo = () => {
    switch (state) {
      case 'ready':
        return { color: 'bg-emerald-500', text: 'Ready', pulse: false };
      case 'listening':
        return { color: 'bg-blue-500', text: 'Listening', pulse: true };
      case 'processing':
        return { color: 'bg-amber-500', text: 'Thinking', pulse: true };
      case 'speaking':
        return { color: 'bg-purple-500', text: 'Speaking', pulse: true };
      case 'connecting':
        return { color: 'bg-slate-400', text: 'Connecting', pulse: true };
      default:
        return { color: 'bg-slate-300', text: 'Disconnected', pulse: false };
    }
  };

  const stateInfo = getStateInfo();

  // Render onboarding steps
  if (!sessionStarted) {
    return (
      <DashboardLayout userName="Learner" streakDays={0} level="--">
        <div className="mx-auto max-w-3xl">
          {/* Progress indicator */}
          <div className="mb-8">
            <div className="flex items-center justify-center gap-2 mb-4">
              {['level', 'mic-test', 'ready'].map((step, idx) => (
                <div key={step} className="flex items-center">
                  <div className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition-all',
                    onboardingStep === step
                      ? 'bg-blue-600 text-white scale-110'
                      : idx < ['level', 'mic-test', 'ready'].indexOf(onboardingStep)
                        ? 'bg-emerald-500 text-white'
                        : 'bg-slate-200 text-slate-500'
                  )}>
                    {idx < ['level', 'mic-test', 'ready'].indexOf(onboardingStep) ? (
                      <CheckCircle2 className="w-5 h-5" />
                    ) : (
                      idx + 1
                    )}
                  </div>
                  {idx < 2 && (
                    <div className={cn(
                      'w-12 h-1 mx-2 rounded',
                      idx < ['level', 'mic-test', 'ready'].indexOf(onboardingStep)
                        ? 'bg-emerald-500'
                        : 'bg-slate-200'
                    )} />
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Step 1: Select Level */}
          {onboardingStep === 'level' && (
            <Card className="border-0 shadow-xl">
              <CardHeader className="text-center pb-2">
                <CardTitle className="text-2xl">What's your English level?</CardTitle>
                <CardDescription className="text-base">
                  We'll adjust the conversation difficulty to match your abilities
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="grid grid-cols-2 gap-3">
                  {LEVELS.map((level) => (
                    <button
                      key={level.id}
                      onClick={() => setSelectedLevel(level.id)}
                      className={cn(
                        'p-4 rounded-xl border-2 text-left transition-all hover:scale-[1.02]',
                        selectedLevel === level.id
                          ? 'border-blue-500 bg-blue-50 shadow-md'
                          : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                      )}
                    >
                      <div className="flex items-center gap-3">
                        <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center text-white font-bold', level.color)}>
                          {level.code}
                        </div>
                        <div>
                          <div className="font-semibold text-slate-900">{level.name}</div>
                          <div className="text-sm text-slate-500">{level.description}</div>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>

                <div className="mt-6 pt-6 border-t">
                  <p className="text-sm text-slate-500 mb-3">Conversation mode</p>
                  <div className="flex gap-3">
                    {CONVERSATION_MODES.map((mode) => (
                      <button
                        key={mode.id}
                        onClick={() => setSelectedMode(mode.id)}
                        className={cn(
                          'flex-1 p-3 rounded-xl border-2 text-left transition-all',
                          selectedMode === mode.id
                            ? 'border-blue-500 bg-blue-50'
                            : 'border-slate-200 hover:border-slate-300'
                        )}
                      >
                        <mode.icon className={cn(
                          'w-5 h-5 mb-2',
                          selectedMode === mode.id ? 'text-blue-600' : 'text-slate-400'
                        )} />
                        <div className="font-medium text-sm">{mode.name}</div>
                        <div className="text-xs text-slate-500">{mode.description}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <Button
                  onClick={() => setOnboardingStep('mic-test')}
                  disabled={selectedLevel === null}
                  className="w-full mt-6 h-12 text-base"
                  size="lg"
                >
                  Continue
                  <ChevronRight className="w-5 h-5 ml-2" />
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Step 2: Microphone Test */}
          {onboardingStep === 'mic-test' && (
            <Card className="border-0 shadow-xl">
              <CardHeader className="text-center pb-2">
                <CardTitle className="text-2xl">Test Your Microphone</CardTitle>
                <CardDescription className="text-base">
                  Make sure we can hear you clearly before starting
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="flex flex-col items-center">
                  {/* Microphone visualization */}
                  <div className="relative mb-8">
                    <div className={cn(
                      'w-32 h-32 rounded-full flex items-center justify-center transition-all',
                      micTested
                        ? audioLevel > 0.1
                          ? 'bg-emerald-100'
                          : 'bg-amber-100'
                        : 'bg-slate-100'
                    )}>
                      {/* Pulse rings */}
                      {micTested && audioLevel > 0.05 && (
                        <>
                          <div
                            className="absolute inset-0 rounded-full bg-emerald-400 opacity-20 animate-ping"
                            style={{ animationDuration: `${1.5 - audioLevel}s` }}
                          />
                          <div
                            className="absolute rounded-full bg-emerald-400 opacity-10"
                            style={{
                              width: `${130 + audioLevel * 50}%`,
                              height: `${130 + audioLevel * 50}%`,
                              transition: 'all 0.1s'
                            }}
                          />
                        </>
                      )}
                      <Mic className={cn(
                        'w-12 h-12 transition-colors',
                        micTested
                          ? audioLevel > 0.1
                            ? 'text-emerald-600'
                            : 'text-amber-600'
                          : 'text-slate-400'
                      )} />
                    </div>
                  </div>

                  {/* Audio level bars */}
                  <div className="flex items-end gap-1 h-16 mb-6">
                    {Array.from({ length: 20 }).map((_, i) => (
                      <div
                        key={i}
                        className={cn(
                          'w-2 rounded-full transition-all duration-75',
                          i / 20 < audioLevel ? 'bg-emerald-500' : 'bg-slate-200'
                        )}
                        style={{
                          height: `${Math.max(8, Math.sin(i * 0.5) * 20 + (i / 20 < audioLevel ? audioLevel * 60 : 0))}px`
                        }}
                      />
                    ))}
                  </div>

                  {/* Status message */}
                  <div className="text-center mb-6">
                    {!micTested ? (
                      <p className="text-slate-500">Click the button below to test your microphone</p>
                    ) : audioLevel > 0.1 ? (
                      <div className="flex items-center gap-2 text-emerald-600">
                        <CheckCircle2 className="w-5 h-5" />
                        <span className="font-medium">Great! We can hear you clearly</span>
                      </div>
                    ) : audioLevel > 0.02 ? (
                      <div className="flex items-center gap-2 text-amber-600">
                        <AlertCircle className="w-5 h-5" />
                        <span className="font-medium">Speak a bit louder</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 text-amber-600">
                        <AlertCircle className="w-5 h-5" />
                        <span className="font-medium">Say something to test...</span>
                      </div>
                    )}
                  </div>

                  {error && (
                    <div className="mb-4 p-3 rounded-lg bg-red-50 text-red-600 text-sm flex items-center gap-2">
                      <AlertCircle className="w-4 h-4" />
                      {error}
                    </div>
                  )}

                  <div className="flex gap-3 w-full">
                    <Button
                      variant="outline"
                      onClick={() => {
                        stopMicTest();
                        setOnboardingStep('level');
                      }}
                      className="flex-1 h-12"
                    >
                      Back
                    </Button>
                    {!micTested ? (
                      <Button onClick={testMicrophone} className="flex-1 h-12">
                        <Mic className="w-5 h-5 mr-2" />
                        Test Microphone
                      </Button>
                    ) : (
                      <Button
                        onClick={() => {
                          stopMicTest();
                          setOnboardingStep('ready');
                        }}
                        className="flex-1 h-12"
                      >
                        Continue
                        <ChevronRight className="w-5 h-5 ml-2" />
                      </Button>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Step 3: Ready to start */}
          {onboardingStep === 'ready' && (
            <Card className="border-0 shadow-xl">
              <CardHeader className="text-center pb-2">
                <CardTitle className="text-2xl">You're All Set!</CardTitle>
                <CardDescription className="text-base">
                  Ready to practice your English with AI coach
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-2xl p-6 mb-6">
                  <div className="flex items-center gap-4 mb-4">
                    <div className={cn(
                      'w-14 h-14 rounded-xl flex items-center justify-center text-white font-bold text-lg',
                      LEVELS[selectedLevel || 0].color
                    )}>
                      {LEVELS[selectedLevel || 0].code}
                    </div>
                    <div>
                      <div className="font-semibold text-lg">{LEVELS[selectedLevel || 0].name}</div>
                      <div className="text-slate-500">{CONVERSATION_MODES.find(m => m.id === selectedMode)?.name}</div>
                    </div>
                  </div>

                  <div className="space-y-2 text-sm text-slate-600">
                    <div className="flex items-center gap-2">
                      <Zap className="w-4 h-4 text-amber-500" />
                      <span>Real-time speech recognition</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 text-blue-500" />
                      <span>Natural conversation with AI</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Volume2 className="w-4 h-4 text-purple-500" />
                      <span>Voice responses from coach</span>
                    </div>
                  </div>
                </div>

                <div className="flex gap-3">
                  <Button
                    variant="outline"
                    onClick={() => setOnboardingStep('mic-test')}
                    className="flex-1 h-12"
                  >
                    Back
                  </Button>
                  <Button
                    onClick={connect}
                    disabled={state === 'connecting'}
                    className="flex-1 h-12 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700"
                    size="lg"
                  >
                    {state === 'connecting' ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                        Connecting...
                      </>
                    ) : (
                      <>
                        <Phone className="w-5 h-5 mr-2" />
                        Start Session
                      </>
                    )}
                  </Button>
                </div>

                {error && (
                  <div className="mt-4 p-3 rounded-lg bg-red-50 text-red-600 text-sm flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </DashboardLayout>
    );
  }

  // Main conversation UI
  return (
    <DashboardLayout userName="Learner" streakDays={0} level={LEVELS[selectedLevel || 0].code}>
      <div className="mx-auto max-w-4xl h-[calc(100vh-8rem)] flex flex-col">
        {/* Header bar */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={cn(
              'w-10 h-10 rounded-lg flex items-center justify-center text-white font-bold text-sm',
              LEVELS[selectedLevel || 0].color
            )}>
              {LEVELS[selectedLevel || 0].code}
            </div>
            <div>
              <h1 className="font-semibold text-lg">Practice Session</h1>
              <p className="text-sm text-slate-500">{CONVERSATION_MODES.find(m => m.id === selectedMode)?.name}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-100">
              <div className={cn(
                'w-2 h-2 rounded-full',
                stateInfo.color,
                stateInfo.pulse && 'animate-pulse'
              )} />
              <span className="text-sm font-medium text-slate-600">{stateInfo.text}</span>
            </div>
            <Button variant="outline" size="sm" onClick={resetConversation}>
              <RotateCcw className="w-4 h-4 mr-1" />
              Reset
            </Button>
            <Button variant="outline" size="sm" onClick={disconnect} className="text-red-600 hover:text-red-700">
              <PhoneOff className="w-4 h-4 mr-1" />
              End
            </Button>
          </div>
        </div>

        {/* Messages */}
        <Card className="flex-1 flex flex-col overflow-hidden border-slate-200">
          <ScrollArea className="flex-1 p-4">
            <div className="space-y-4">
              {messages.length === 0 && !streamingResponse ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center mb-4">
                    <Mic className="w-8 h-8 text-blue-600" />
                  </div>
                  <h3 className="font-semibold text-lg text-slate-700 mb-2">Ready to practice!</h3>
                  <p className="text-slate-500 max-w-sm">
                    Click the microphone button and start speaking. I'll listen and respond to help you practice your English.
                  </p>
                </div>
              ) : (
                <>
                  {messages.map((msg) => (
                    <div
                      key={msg.id}
                      className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}
                    >
                      <div
                        className={cn(
                          'rounded-2xl px-4 py-3 max-w-[80%] shadow-sm',
                          msg.role === 'user'
                            ? 'bg-blue-600 text-white rounded-br-md'
                            : 'bg-white border border-slate-200 text-slate-800 rounded-bl-md'
                        )}
                      >
                        <p className="text-[15px] leading-relaxed">{msg.text}</p>
                        <div className={cn(
                          'flex items-center gap-2 mt-1 text-xs',
                          msg.role === 'user' ? 'text-blue-200' : 'text-slate-400'
                        )}>
                          <Clock className="w-3 h-3" />
                          {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          {msg.confidence && (
                            <span className="ml-2">{Math.round(msg.confidence * 100)}% confidence</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                  {streamingResponse && (
                    <div className="flex justify-start">
                      <div className="rounded-2xl rounded-bl-md px-4 py-3 max-w-[80%] bg-white border border-slate-200 shadow-sm">
                        <p className="text-[15px] leading-relaxed text-slate-800">{streamingResponse}</p>
                        <div className="flex items-center gap-1 mt-2">
                          <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" />
                          <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0.1s' }} />
                          <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0.2s' }} />
                        </div>
                      </div>
                    </div>
                  )}
                </>
              )}
              <div ref={messagesEndRef} />
            </div>
          </ScrollArea>
        </Card>

        {/* Controls */}
        <div className="mt-4 p-4 bg-white rounded-2xl border border-slate-200 shadow-sm">
          <div className="flex items-center justify-center gap-6">
            {/* Audio level indicator */}
            <div className="flex items-center gap-1 w-24">
              {isRecording && (
                <>
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div
                      key={i}
                      className={cn(
                        'w-1.5 rounded-full transition-all duration-75',
                        i / 8 < audioLevel ? 'bg-emerald-500' : 'bg-slate-200'
                      )}
                      style={{
                        height: `${12 + (i / 8 < audioLevel ? audioLevel * 24 : 0)}px`
                      }}
                    />
                  ))}
                </>
              )}
            </div>

            {/* Main mic button */}
            <button
              onClick={toggleRecording}
              disabled={state === 'processing' || state === 'speaking'}
              className={cn(
                'relative w-20 h-20 rounded-full flex items-center justify-center transition-all',
                'focus:outline-none focus:ring-4 focus:ring-blue-200',
                isRecording
                  ? 'bg-red-500 hover:bg-red-600 text-white'
                  : state === 'processing' || state === 'speaking'
                    ? 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    : 'bg-blue-600 hover:bg-blue-700 text-white hover:scale-105'
              )}
            >
              {/* Pulse animation when recording */}
              {isRecording && (
                <div className="absolute inset-0 rounded-full bg-red-400 animate-ping opacity-30" />
              )}
              {isRecording ? (
                <MicOff className="w-8 h-8 relative z-10" />
              ) : (
                <Mic className="w-8 h-8 relative z-10" />
              )}
            </button>

            {/* Right side spacer for symmetry */}
            <div className="w-24" />
          </div>

          {/* Status text */}
          <p className="text-center text-sm text-slate-500 mt-3">
            {isRecording ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                Listening... Speak clearly
              </span>
            ) : state === 'processing' ? (
              <span className="flex items-center justify-center gap-2">
                <div className="w-4 h-4 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                Processing your speech...
              </span>
            ) : state === 'speaking' ? (
              <span className="flex items-center justify-center gap-2">
                <Volume2 className="w-4 h-4 text-purple-500" />
                Coach is speaking...
              </span>
            ) : (
              'Tap the microphone to speak'
            )}
          </p>
        </div>

        {/* Metrics */}
        {metrics && (
          <div className="mt-3 flex justify-center gap-4 text-xs text-slate-500">
            <span>STT: {metrics.stt_time.toFixed(2)}s</span>
            <span>LLM: {metrics.llm_time.toFixed(2)}s</span>
            <span>TTS: {metrics.tts_time.toFixed(2)}s</span>
            <span className={cn(
              'font-medium',
              metrics.total_time < 2 ? 'text-emerald-600' : 'text-amber-600'
            )}>
              Total: {metrics.total_time.toFixed(2)}s
            </span>
          </div>
        )}

        {/* Error display */}
        {error && (
          <div className="mt-3 p-3 rounded-lg bg-red-50 text-red-600 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
