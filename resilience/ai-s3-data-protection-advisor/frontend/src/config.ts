export interface AppConfig {
  region: string;
  userPoolId: string;
  userPoolClientId: string;
  /** Deployed with --multi-account: Start scan offers the organization scope. */
  multiAccount: boolean;
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConfigError';
  }
}

let loaded: AppConfig | null = null;
let pending: Promise<AppConfig> | null = null;

function validate(raw: unknown): AppConfig {
  const o = (raw ?? {}) as Record<string, unknown>;
  for (const key of ['region', 'userPoolId', 'userPoolClientId'] as const) {
    if (typeof o[key] !== 'string' || !(o[key] as string).trim()) {
      throw new ConfigError(`Configuration field "${key}" is missing.`);
    }
  }
  return {
    region: o.region as string,
    userPoolId: o.userPoolId as string,
    userPoolClientId: o.userPoolClientId as string,
    multiAccount: o.multiAccount === true,
  };
}

async function fetchConfig(): Promise<AppConfig> {
  let res: Response;
  try {
    res = await fetch('/config.json');
  } catch {
    throw new ConfigError('The configuration file could not be fetched.');
  }
  if (!res.ok) throw new ConfigError(`The configuration file returned status ${res.status}.`);
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new ConfigError('The configuration file is not valid JSON.');
  }
  return validate(body);
}

/** Fetches /config.json once; later calls return the same value. */
export function loadConfig(): Promise<AppConfig> {
  if (loaded) return Promise.resolve(loaded);
  if (!pending) {
    pending = fetchConfig().then(
      (cfg) => {
        loaded = cfg;
        return cfg;
      },
      (err) => {
        pending = null;
        throw err;
      },
    );
  }
  return pending;
}

/** Synchronous access once loadConfig() has resolved. */
export function getConfig(): AppConfig {
  if (!loaded) throw new ConfigError('The configuration has not been loaded.');
  return loaded;
}

export function resetConfig(): void {
  loaded = null;
  pending = null;
}
