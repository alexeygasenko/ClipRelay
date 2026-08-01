const JSON_HEADERS = {
  Accept: "application/json",
  "X-Requested-With": "fetch",
};

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function isJson(response) {
  return (response.headers.get("content-type") || "").includes("application/json");
}

async function readPayload(response) {
  if (response.status === 204) return {};
  if (isJson(response)) return response.json();
  const text = await response.text();
  return text ? { text } : {};
}

export async function request(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...JSON_HEADERS,
      ...(options.headers || {}),
    },
  });
  const payload = await readPayload(response);
  if (!response.ok) {
    throw new ApiError(
      payload.error
        || payload.message
        || payload.detail
        || `Ошибка запроса (${response.status})`,
      response.status,
      payload,
    );
  }
  if (response.redirected && !payload.redirect) {
    payload.redirect = new URL(response.url).pathname;
  }
  return payload;
}

export function getJson(url, signal) {
  return request(url, { signal });
}

export function postForm(url, values = {}, options = {}) {
  const body = values instanceof FormData ? values : new URLSearchParams();
  if (!(values instanceof FormData)) {
    Object.entries(values).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.forEach((item) => body.append(key, String(item)));
      } else if (value !== undefined && value !== null) {
        body.append(key, String(value));
      }
    });
  }
  return request(url, {
    method: "POST",
    body,
    signal: options.signal,
  });
}

export function pathFromUrl(value, fallback = "/") {
  if (!value) return fallback;
  try {
    const url = new URL(value, window.location.origin);
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

export function editorRouteFromUrl(value, fallbackSource = "tiktok") {
  const path = pathFromUrl(value, "");
  const patterns = [
    [/\/media\/post\/([^/?#]+)/, (id) => `/media/post/${id}`],
    [/\/youtube\/post\/([^/?#]+)/, (id) => `/youtube/post/${id}`],
    [/\/spotify\/post\/([^/?#]+)/, (id) => `/spotify/post/${id}`],
  ];
  for (const [pattern, build] of patterns) {
    const match = path.match(pattern);
    if (match) {
      const route = build(encodeURIComponent(match[1]));
      const query = new URL(value, window.location.origin).search;
      return `${route}${query}`;
    }
  }
  return `/?source=${encodeURIComponent(fallbackSource)}`;
}
