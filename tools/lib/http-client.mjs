import https from "node:https";
import { REQUEST_TIMEOUT_MS, sleep } from "./shared.mjs";

const KEEP_ALIVE_AGENT = new https.Agent({ keepAlive: true });
const RETRY_STATUS_CODES = new Set([429, 500, 502, 503, 504]);

function createHttpError(message, statusCode) {
  const error = new Error(message);
  error.statusCode = statusCode;
  return error;
}

function resolveRedirectLocation(baseUrl, location) {
  try {
    return new URL(location, baseUrl).toString();
  } catch {
    return null;
  }
}

function doRequest(url, options = {}) {
  const {
    headers = {},
    timeoutMs = REQUEST_TIMEOUT_MS
  } = options;

  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      {
        agent: KEEP_ALIVE_AGENT,
        headers: {
          "User-Agent": "ranking-xadrez-jovem/2.0",
          ...headers
        }
      },
      (response) => {
        const { statusCode = 500, headers: responseHeaders = {} } = response;

        if ([301, 302, 303, 307, 308].includes(statusCode) && responseHeaders.location) {
          response.resume();
          const redirectedUrl = resolveRedirectLocation(url, responseHeaders.location);
          if (!redirectedUrl) {
            reject(createHttpError(`Redirecionamento inválido para ${url}`, statusCode));
            return;
          }
          resolve(doRequest(redirectedUrl, options));
          return;
        }

        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (statusCode < 200 || statusCode >= 300) {
            reject(createHttpError(`HTTP ${statusCode} para ${url}`, statusCode));
            return;
          }

          resolve(body);
        });
      }
    );

    request.setTimeout(timeoutMs, () => {
      request.destroy(createHttpError(`Timeout para ${url}`, 408));
    });

    request.on("error", reject);
  });
}

export async function requestText(url, options = {}) {
  const {
    retries = 3,
    retryDelayMs = 350,
    retryOnStatusCodes = []
  } = options;

  let lastError;
  const retryableStatuses = new Set([...RETRY_STATUS_CODES, ...retryOnStatusCodes]);

  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      return await doRequest(url, options);
    } catch (error) {
      lastError = error;
      const shouldRetry =
        attempt < retries &&
        (retryableStatuses.has(error?.statusCode) || !error?.statusCode);

      if (!shouldRetry) {
        throw error;
      }

      await sleep(retryDelayMs * attempt);
    }
  }

  throw lastError;
}

export async function requestJson(url, options = {}) {
  const text = await requestText(url, options);
  try {
    return JSON.parse(text || "{}");
  } catch (error) {
    throw createHttpError(`JSON inválido para ${url}: ${error.message}`, 500);
  }
}
