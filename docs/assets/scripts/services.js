import { VISIT_COUNTER_CONFIG } from "./config.js";

export async function loadSourcePayload(sourceConfig) {
  const response = await fetch(`./${sourceConfig.file}?v=${Date.now()}`, {
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`Falha ao buscar ${sourceConfig.file}: HTTP ${response.status}`);
  }

  return response.json();
}

export async function incrementVisitCounter() {
  const endpoint =
    `${VISIT_COUNTER_CONFIG.apiBaseUrl}/` +
    `${encodeURIComponent(VISIT_COUNTER_CONFIG.namespace)}/` +
    `${encodeURIComponent(VISIT_COUNTER_CONFIG.key)}/up`;

  const response = await fetch(endpoint, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Falha no contador de visitas: HTTP ${response.status}`);
  }

  const payload = await response.json();
  return Number(payload?.count ?? payload?.value);
}
