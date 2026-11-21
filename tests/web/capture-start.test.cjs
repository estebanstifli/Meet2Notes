const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '../../src/local_meeting_ai/web/static/js', name), 'utf8');
const slice = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const transcript = read('transcript.js');
const capture = slice(transcript, '  function transcriptionOptions(', '  async function startFileTranscription(');
const app = read('app.js');
const apiSource = slice(app, '  async function api(', '  function escapeHTML(');

const confirmation = Object.assign(
  new Error('The small model is not installed. Confirm the model download to continue.'),
  { status: 409, code: 'ConfirmationRequiredError' },
);
function workspace({ confirm, failures = [confirmation] }) {
  const form = { dataset: { busy: 'true' } };
  const submit = { disabled: true };
  const state = {
    draftTitle: 'Weekly sync', requests: [], prompts: [], notifications: [], page: { dataset: {} },
    selectedNativeSources: () => [{ value: 'mic-1' }],
    api: async (url, options) => {
      state.requests.push(JSON.parse(options.body));
      if (failures.length) throw failures.shift();
      return { meeting_id: 7, transcription_id: 9, session_id: 'live-1', title: 'Weekly sync' };
    },
    window: {
      confirm: message => { state.prompts.push(message); return confirm; },
      history: { replaceState() {} },
    },
    document: { querySelector: selector => (selector === '#transcription-form' ? form : submit) },
    setTitle() {}, setLiveState() {}, selectTranscription: async () => {},
    toast: message => state.notifications.push(message),
  };
  vm.createContext(state);
  vm.runInContext(`let captureSession, meetingId, activeTranscriptionId, lastLiveSegmentCount;\n${capture}`, state);
  return state;
}

test('a missing live model asks once and retries with the download allowed', async () => {
  const state = workspace({ confirm: true });
  await state.startNativeCapture();
  assert.deepEqual(state.requests.map(request => request.allow_model_download), [false, true]);
  assert.equal(state.prompts.length, 1);
  assert.match(state.prompts[0], /small model is not installed/);
  assert.deepEqual(state.notifications, ['Real-time local transcription started.']);
});
test('declining the download cancels quietly without a second request', async () => {
  const state = workspace({ confirm: false });
  await assert.rejects(state.startNativeCapture(), error => error.cancelled === true);
  assert.equal(state.requests.length, 1);
});
test('other errors are not turned into a download prompt', async () => {
  const busy = Object.assign(new Error('Another live transcription is already active'), { status: 400, code: 'ValidationError' });
  const state = workspace({ confirm: true, failures: [busy] });
  await assert.rejects(state.startNativeCapture(), /already active/);
  assert.equal(state.prompts.length, 0);
});
test('api errors carry the server error type and status', async () => {
  const state = {
    performance: { now: () => 0 }, performanceLogThresholdMs: 1000, timingLog() {}, Headers, FormData,
    fetch: async () => new Response(
      JSON.stringify({ detail: 'Confirm the model download to continue.', error: 'ConfirmationRequiredError' }),
      { status: 409, headers: { 'content-type': 'application/json' } },
    ),
  };
  vm.createContext(state);
  vm.runInContext(apiSource, state);
  await assert.rejects(state.api('/api/capture/sessions', { method: 'POST', body: '{}' }), error => {
    assert.equal(error.message, 'Confirm the model download to continue.');
    assert.equal(error.code, 'ConfirmationRequiredError');
    assert.equal(error.status, 409);
    return true;
  });
});
