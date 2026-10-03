// SPDX-License-Identifier: GPL-3.0-or-later
// Boundary between the screens (app.js) and the data source.
// #28 provides only the display-only mock. #29 adds an HTTP implementation with
// the same methods; there is intentionally no fallback from the mock to the
// real API, so a demo action can never start a receiver.
(function () {
  'use strict';

  class ApiError extends Error {
    constructor(status, code) {
      super(code);
      this.status = status; // 0 means the server could not be reached
      this.code = code;
    }
  }

  function wrap(backend) {
    const run = (name, ...args) => backend[name](...args).catch(error => {
      throw new ApiError(error && error.status !== undefined ? error.status : 0,
        error && error.code ? error.code : 'unknown_error');
    });
    const requestId = () => crypto.randomUUID();
    return {
      mode: backend.mode,
      bootstrap: () => run('bootstrap'),
      status: () => run('status'),
      diagnostics: inputKind => run('diagnostics', inputKind),
      services: () => run('services'),
      // Callers keep the request_id while retrying the same user action.
      newRequestId: requestId,
      startScan: (channels, request_id) => run('startScan', {request_id, channels}),
      getScan: id => run('getScan', id),
      stopScan: id => run('stopScan', id),
      startSession: (serviceRef, request_id) => run('startSession', {request_id, service_ref: serviceRef}),
      getSession: id => run('getSession', id),
      stopSession: id => run('stopSession', id),
      startRecording: (sessionId, request_id) => run('startRecording', {request_id, session_id: sessionId}),
      getRecording: id => run('getRecording', id),
      stopRecording: id => run('stopRecording', id),
      recordings: () => run('recordings'),
      demo: backend.mode === 'mock' ? backend : null,
    };
  }

  function create(options) {
    if (options.mode !== 'mock') {
      throw new Error('Only the display-only mock is implemented; the HTTP API is #29');
    }
    return wrap(window.SdrMock.createMockBackend(options));
  }

  window.SdrApi = {create, ApiError};
})();
