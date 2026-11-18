import { ALL_FORMATS, AudioSampleSink, BlobSource, Input } from 'mediabunny';

const MODEL = 'onnx-community/whisper-tiny';
const SAMPLE_RATE = 16000;
const MAX_DURATION_SECONDS = 600;
let recognizerPromise = null;

function sendProgress(phase, detail, progress) {
  self.postMessage({ type: 'progress', phase, detail, progress });
}

async function getRecognizer() {
  if (recognizerPromise) return recognizerPromise;
  const { env, pipeline } = await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2');
  env.useBrowserCache = true;
  env.useWasmCache = true;
  env.allowLocalModels = false;
  const progress_callback = info => {
    if (info.status === 'progress_total') {
      sendProgress('model', `Downloading the local model (${Math.round(info.progress)}%)`, info.progress);
    } else if (info.status === 'initiate') {
      sendProgress('model', 'Preparing the local transcription model…', 0);
    }
  };
  recognizerPromise = (async () => {
    if (typeof navigator !== 'undefined' && navigator.gpu) {
      try {
        sendProgress('model', 'Loading the model for your GPU…', 0);
        return await pipeline('automatic-speech-recognition', MODEL, {
          device: 'webgpu',
          dtype: 'fp16',
          progress_callback,
        });
      } catch (error) {
        sendProgress('model', 'GPU acceleration is unavailable; switching to the quantized CPU model…', 0);
      }
    }
    return pipeline('automatic-speech-recognition', MODEL, {
      device: 'wasm',
      dtype: 'q8',
      progress_callback,
    });
  })().catch(error => {
    recognizerPromise = null;
    throw error;
  });
  return recognizerPromise;
}

async function extractAudio(blob, clip, requestId) {
  const input = new Input({ source: new BlobSource(blob), formats: ALL_FORMATS });
  try {
    const track = await input.getPrimaryAudioTrack();
    if (!track) throw new Error('No audio track was found in the selected video.');

    const sourceDuration = await track.getDurationFromMetadata() ?? await track.computeDuration();
    const trim = Math.max(0, Number(clip.trim) || 0);
    const speed = Number(clip.speed == null ? 1 : clip.speed);
    if (!Number.isFinite(speed) || speed <= 0) {
      throw new Error('This clip uses animated playback speed. Set a constant speed before transcribing it.');
    }
    const requestedDuration = clip.length === 'auto' || clip.length == null
      ? Math.max(0, sourceDuration - trim) / speed
      : Number(clip.length);
    if (!Number.isFinite(requestedDuration) || requestedDuration <= 0) {
      throw new Error('The selected video has no usable duration.');
    }
    if (requestedDuration > MAX_DURATION_SECONDS) {
      throw new Error('On-device transcription is limited to 10 minutes per clip. Trim the video and try again.');
    }

    const duration = Math.min(requestedDuration, Math.max(0, sourceDuration - trim) / speed);
    const end = Math.min(sourceDuration, trim + duration * speed);
    const samples = new Float32Array(Math.ceil(duration * SAMPLE_RATE));
    const sink = new AudioSampleSink(track);
    let receivedSamples = 0;
    for await (const sample of sink.samples(trim, end)) {
      try {
        const channelData = Array.from({ length: sample.numberOfChannels }, (_, channelIndex) => {
          const plane = new Float32Array(sample.numberOfFrames);
          sample.copyTo(plane, { planeIndex: channelIndex, format: 'f32-planar' });
          return plane;
        });
        const outStart = Math.max(0, Math.round(((sample.timestamp - trim) / speed) * SAMPLE_RATE));
        const outEnd = Math.min(samples.length,
          Math.ceil(outStart + sample.numberOfFrames * SAMPLE_RATE / (sample.sampleRate * speed)));
        for (let outIndex = outStart; outIndex < outEnd; outIndex++) {
          const sourceFrame = (outIndex / SAMPLE_RATE * speed + trim - sample.timestamp) * sample.sampleRate;
          const frame = Math.max(0, Math.min(sample.numberOfFrames - 1, Math.floor(sourceFrame)));
          const nextFrame = Math.min(sample.numberOfFrames - 1, frame + 1);
          const fraction = Math.max(0, Math.min(1, sourceFrame - frame));
          let mono = 0;
          for (const channel of channelData) {
            mono += channel[frame] * (1 - fraction) + channel[nextFrame] * fraction;
          }
          samples[outIndex] = mono / channelData.length;
        }
        receivedSamples += outEnd - outStart;
      } finally {
        sample.close();
      }
      if (requestId) {
        sendProgress('audio', 'Reading audio on this device…', Math.min(100, receivedSamples / samples.length * 100));
      }
    }
    if (!receivedSamples) throw new Error('Could not decode audio from this video in your browser.');
    return { samples, duration };
  } finally {
    input.dispose();
  }
}

async function transcribe(blob, clip, requestId) {
  if (!(blob instanceof Blob) || !blob.size) throw new Error('The selected video file is empty or unavailable.');
  sendProgress('audio', 'Preparing audio locally…', 0);
  const { samples, duration } = await extractAudio(blob, clip, requestId);
  const recognizer = await getRecognizer();
  const words = [];
  const windowSeconds = 30;
  const overlapSeconds = 5;
  const stepSeconds = windowSeconds - overlapSeconds;
  const totalWindows = Math.ceil(Math.max(0, duration - overlapSeconds) / stepSeconds);

  for (let windowIndex = 0, start = 0; start < duration; windowIndex++, start += stepSeconds) {
    const end = Math.min(duration, start + windowSeconds);
    const audio = samples.subarray(Math.floor(start * SAMPLE_RATE), Math.ceil(end * SAMPLE_RATE));
    sendProgress('transcribe', `Transcribing on this device… (${windowIndex + 1}/${totalWindows})`,
      (windowIndex / totalWindows) * 100);
    const result = await recognizer(audio, { return_timestamps: 'word' });
    for (const chunk of result.chunks || []) {
      const [wordStart, wordEnd] = chunk.timestamp || [];
      if (!Number.isFinite(wordStart) || !Number.isFinite(wordEnd) || !chunk.text.trim()) continue;
      if (windowIndex > 0 && wordStart < overlapSeconds) continue;
      const relativeStart = start + wordStart;
      const relativeEnd = Math.min(duration, start + wordEnd);
      if (relativeEnd <= relativeStart) continue;
      words.push({
        text: chunk.text.trim(),
        start: Math.round(relativeStart * 1000),
        end: Math.round(relativeEnd * 1000),
      });
    }
  }
  if (!words.length) throw new Error('No speech was detected. Try a clip with clearer speech.');
  sendProgress('done', 'Transcription complete.', 100);
  return { words, duration, wordCount: words.length };
}

self.onmessage = async event => {
  const { id, blob, clip } = event.data || {};
  if (!id) return;
  try {
    const result = await transcribe(blob, clip, id);
    self.postMessage({ type: 'done', id, ...result });
  } catch (error) {
    self.postMessage({ type: 'error', id, message: error.message || 'On-device transcription failed.' });
  }
};
