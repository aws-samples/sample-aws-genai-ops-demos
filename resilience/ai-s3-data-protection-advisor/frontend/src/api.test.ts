import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./auth', () => ({
  getIdToken: vi.fn(),
  signOut: vi.fn(),
}));

import { getIdToken, signOut } from './auth';
import { ApiError, fetchReport, reportUrl, request, setUnauthorizedHandler, startScan } from './api';

function reply(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(() => {
  vi.mocked(getIdToken).mockReset();
  vi.mocked(getIdToken).mockResolvedValue('tok-123');
  vi.mocked(signOut).mockClear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

describe('request', () => {
  it('adds the Authorization header and returns the parsed body', async () => {
    const fn = reply(200, { runs: [] });
    expect(await request('/api/scans')).toEqual({ runs: [] });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe('/api/scans');
    expect(new Headers(init.headers).get('Authorization')).toBe('tok-123');
  });

  it('on 401 signs out, calls the unauthorized handler and throws ApiError', async () => {
    reply(401, { message: 'Unauthorized' });
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(request('/api/scans')).rejects.toMatchObject({ status: 401 });
    expect(signOut).toHaveBeenCalled();
    expect(handler).toHaveBeenCalled();
  });

  it('maps error bodies to ApiError with status and message', async () => {
    reply(409, { error: 'A scan is already running', run_id: 'r1' });
    const err = (await request('/api/scans', { method: 'POST' }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe('A scan is already running');
    expect(err.runId).toBe('r1');
  });

  it('keeps the run id of a 409 on the error (startScan)', async () => {
    reply(409, { error: 'A scan is already running', run_id: 'run-77' });
    const err = (await startScan().catch((e) => e)) as ApiError;
    expect(err.status).toBe(409);
    expect(err.runId).toBe('run-77');
  });

  it('maps statuses to sentence-case texts', async () => {
    for (const [status, text] of [[400, 'The request was not valid.'], [404, 'Not found.'], [500, 'Something went wrong on the server. Try again.'], [503, 'Something went wrong on the server. Try again.']] as const) {
      reply(status, { error: 'internal detail' });
      const err = (await request('/api/x').catch((e) => e)) as ApiError;
      expect(err.message).toBe(text);
    }
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => { throw new Error('bad json'); },
    }));
    const err = (await request('/api/scans').catch((e) => e)) as ApiError;
    expect(err).toMatchObject({ status: 500, message: 'Something went wrong on the server. Try again.' });
  });

  it('fails with a 401 ApiError when there is no token', async () => {
    vi.mocked(getIdToken).mockResolvedValue(null);
    const fn = reply(200, {});
    await expect(request('/api/scans')).rejects.toMatchObject({ status: 401 });
    expect(fn).not.toHaveBeenCalled();
  });
});

describe('typed calls', () => {
  it('startScan posts and returns the run id', async () => {
    const fn = reply(202, { run_id: 'r9' });
    expect(await startScan()).toEqual({ run_id: 'r9' });
    expect(fn.mock.calls[0][1].method).toBe('POST');
  });

  it('reportUrl asks for the format', async () => {
    const fn = reply(200, { url: 'https://x', expires_in: 900 });
    await reportUrl('r1', 'xlsx');
    expect(fn.mock.calls[0][0]).toBe('/api/scans/r1/report?format=xlsx');
  });

  it('fetchReport sends no Authorization header', async () => {
    const fn = reply(200, { meta: {}, tabs: [] });
    await fetchReport('https://bucket.s3.amazonaws.com/x?sig=1');
    const init = fn.mock.calls[0][1];
    expect(init?.headers ?? {}).not.toHaveProperty('Authorization');
    expect(getIdToken).not.toHaveBeenCalled();
  });
});
