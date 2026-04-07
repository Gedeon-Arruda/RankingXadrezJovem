import {
  DEFAULTS,
  MOBILE_MEDIA_QUERY,
  SOURCE_CONFIG,
  SOURCE_STORAGE_KEY
} from "./config.js";
import { incrementVisitCounter, loadSourcePayload } from "./services.js";
import {
  dedupePlayers,
  extractPlayers,
  filterPlayers,
  formatInfoLine,
  hasActiveFilters,
  isValidSelectValue,
  sortPlayers
} from "./utils.js";
import {
  renderDesktopRows,
  renderEmptyState,
  renderMobileCards,
  renderPager
} from "./renderers.js";

const state = {
  source: DEFAULTS.source,
  allPlayers: [],
  filteredPlayers: [],
  generatedAt: null,
  page: 1,
  requestId: 0
};

const elements = {
  body: document.body,
  sourceTabs: [...document.querySelectorAll(".source-tab")],
  search: document.getElementById("q"),
  sort: document.getElementById("sort"),
  order: document.getElementById("order"),
  perPage: document.getElementById("perPage"),
  info: document.getElementById("info"),
  totalBadge: document.getElementById("totalBadge"),
  visitBadge: document.getElementById("visitBadge"),
  hero: document.getElementById("hero"),
  heroTitle: document.getElementById("heroTitle"),
  heroDescription: document.getElementById("heroDescription"),
  joinTeamBtn: document.getElementById("joinTeamBtn"),
  dismissJoinBanner: document.getElementById("dismissJoinBanner"),
  tbody: document.getElementById("tbody"),
  cardList: document.getElementById("cardList"),
  pager: document.getElementById("pager"),
  footer: document.getElementById("footerText"),
  tableWrap: document.getElementById("tableWrap"),
  backToTop: document.getElementById("backToTop")
};

function getSourceConfig() {
  return SOURCE_CONFIG[state.source] || SOURCE_CONFIG[DEFAULTS.source];
}

function readStoredSource() {
  try {
    return localStorage.getItem(SOURCE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistSource(source) {
  try {
    localStorage.setItem(SOURCE_STORAGE_KEY, source);
  } catch {
    // ignore storage errors
  }
}

function updateSourceUi() {
  const sourceConfig = getSourceConfig();
  elements.body.dataset.source = sourceConfig.id;

  for (const tab of elements.sourceTabs) {
    const isActive = tab.dataset.source === sourceConfig.id;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
  }

  elements.heroTitle.textContent = sourceConfig.heroTitle;
  elements.heroDescription.innerHTML = sourceConfig.heroDescription;
  elements.joinTeamBtn.href = sourceConfig.ctaUrl;
  elements.joinTeamBtn.textContent = sourceConfig.ctaLabel;
  elements.footer.textContent = sourceConfig.footerText;
}

function restoreState() {
  const storedSource = readStoredSource();
  if (storedSource && SOURCE_CONFIG[storedSource]) {
    state.source = storedSource;
  }

  const params = new URLSearchParams(window.location.search);
  const sourceParam = params.get("source");
  if (sourceParam && SOURCE_CONFIG[sourceParam]) {
    state.source = sourceParam;
  }

  const queryParam = params.get("q");
  if (queryParam !== null) {
    elements.search.value = queryParam;
  }

  const sortParam = params.get("sort");
  if (sortParam && isValidSelectValue(elements.sort, sortParam)) {
    elements.sort.value = sortParam;
  }

  const orderParam = params.get("order");
  if (orderParam && isValidSelectValue(elements.order, orderParam)) {
    elements.order.value = orderParam;
  }

  const perPageParam = params.get("perPage");
  if (perPageParam && isValidSelectValue(elements.perPage, perPageParam)) {
    elements.perPage.value = perPageParam;
  }

  const pageParam = Number(params.get("page"));
  if (Number.isFinite(pageParam) && pageParam >= 1) {
    state.page = Math.floor(pageParam);
  }
}

function syncUrl() {
  const params = new URLSearchParams();
  params.set("source", state.source);

  const query = elements.search.value.trim();
  if (query) {
    params.set("q", query);
  }

  if (elements.sort.value !== DEFAULTS.sort) {
    params.set("sort", elements.sort.value);
  }

  if (elements.order.value !== DEFAULTS.order) {
    params.set("order", elements.order.value);
  }

  if (elements.perPage.value !== DEFAULTS.perPage) {
    params.set("perPage", elements.perPage.value);
  }

  if (state.page > 1) {
    params.set("page", String(state.page));
  }

  const nextQuery = params.toString();
  const nextUrl = nextQuery ? `${window.location.pathname}?${nextQuery}` : window.location.pathname;
  window.history.replaceState(null, "", nextUrl);
}

function clearFilters() {
  elements.search.value = "";
  elements.sort.value = DEFAULTS.sort;
  elements.order.value = DEFAULTS.order;
  elements.perPage.value = DEFAULTS.perPage;
  state.page = 1;
  applyFilters();
}

function setSource(nextSource, { persist = true, resetPage = true } = {}) {
  if (!SOURCE_CONFIG[nextSource]) {
    return;
  }

  state.source = nextSource;

  if (persist) {
    persistSource(nextSource);
  }

  if (resetPage) {
    state.page = 1;
  }

  updateSourceUi();
}

function renderEmpty() {
  const emptyBlock = renderEmptyState(
    hasActiveFilters({
      query: elements.search.value,
      sort: elements.sort.value,
      order: elements.order.value,
      perPage: elements.perPage.value,
      source: state.source
    })
  );

  elements.tbody.innerHTML = `<tr><td colspan="7">${emptyBlock}</td></tr>`;
  elements.cardList.innerHTML = emptyBlock;
  elements.pager.innerHTML = "";
}

function applyResponsiveAria() {
  const isMobile = window.matchMedia(MOBILE_MEDIA_QUERY).matches;
  elements.tableWrap.setAttribute("aria-hidden", String(isMobile));
  elements.cardList.setAttribute("aria-hidden", String(!isMobile));
}

function updateBackToTopVisibility() {
  if (!elements.backToTop) {
    return;
  }

  elements.backToTop.classList.toggle("visible", window.scrollY > 320);
}

function togglePlayerCard(cardElement) {
  if (!cardElement) {
    return;
  }

  const isFlipped = cardElement.classList.toggle("is-flipped");
  cardElement.setAttribute("aria-pressed", String(isFlipped));
}

function render() {
  const sourceConfig = getSourceConfig();
  const perPage = Math.max(1, Number(elements.perPage.value) || Number(DEFAULTS.perPage));
  const totalPages = Math.max(1, Math.ceil(state.filteredPlayers.length / perPage));

  if (state.page > totalPages) {
    state.page = totalPages;
  }

  const startIndex = (state.page - 1) * perPage;
  const pageItems = state.filteredPlayers.slice(startIndex, startIndex + perPage);

  if (pageItems.length === 0) {
    renderEmpty();
  } else {
    elements.tbody.innerHTML = renderDesktopRows(
      pageItems,
      startIndex,
      sourceConfig.id,
      sourceConfig
    );
    elements.cardList.innerHTML = renderMobileCards(
      pageItems,
      startIndex,
      sourceConfig.id,
      sourceConfig
    );
    renderPager(elements.pager, state.page, totalPages, (nextPage) => {
      state.page = nextPage;
      render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  applyResponsiveAria();
  syncUrl();
}

function applyFilters() {
  const query = elements.search.value;
  const filtered = filterPlayers(state.allPlayers, query);
  state.filteredPlayers = sortPlayers(filtered, elements.sort.value, elements.order.value);
  render();
}

async function loadData({ resetPage = false } = {}) {
  const sourceConfig = getSourceConfig();
  const currentRequestId = ++state.requestId;

  elements.info.textContent = `Carregando ${sourceConfig.label}...`;
  elements.totalBadge.textContent = "--";
  elements.tbody.innerHTML = "";
  elements.cardList.innerHTML = "";
  elements.pager.innerHTML = "";

  try {
    const payload = await loadSourcePayload(sourceConfig);
    if (currentRequestId !== state.requestId) {
      return;
    }

    const players = dedupePlayers(extractPlayers(payload));
    players.sort((left, right) => {
      const leftPosition = Number(left?.position ?? Number.MAX_SAFE_INTEGER);
      const rightPosition = Number(right?.position ?? Number.MAX_SAFE_INTEGER);
      return leftPosition - rightPosition;
    });

    state.allPlayers = players;
    state.generatedAt = payload?.generated_at || null;

    if (resetPage) {
      state.page = 1;
    }

    elements.totalBadge.textContent = String(players.length);
    elements.info.textContent = formatInfoLine(
      sourceConfig.label,
      players.length,
      state.generatedAt
    );

    applyFilters();
  } catch (error) {
    if (currentRequestId !== state.requestId) {
      return;
    }

    console.error(error);
    elements.info.textContent = `Erro ao carregar dados de ${sourceConfig.label}`;
    state.allPlayers = [];
    state.filteredPlayers = [];
    renderEmpty();
  }
}

function debounce(callback, delayMs) {
  let timeoutId = 0;
  return (...args) => {
    window.clearTimeout(timeoutId);
    timeoutId = window.setTimeout(() => callback(...args), delayMs);
  };
}

function bindEvents() {
  const debouncedSearch = debounce(() => {
    state.page = 1;
    applyFilters();
  }, 180);

  elements.search.addEventListener("input", debouncedSearch);
  elements.sort.addEventListener("change", () => {
    state.page = 1;
    applyFilters();
  });
  elements.order.addEventListener("change", () => {
    state.page = 1;
    applyFilters();
  });
  elements.perPage.addEventListener("change", () => {
    state.page = 1;
    render();
  });

  for (const tab of elements.sourceTabs) {
    tab.addEventListener("click", () => {
      const nextSource = tab.dataset.source || DEFAULTS.source;
      if (nextSource === state.source) {
        return;
      }

      setSource(nextSource, { persist: true, resetPage: true });
      loadData({ resetPage: true });
    });
  }

  elements.dismissJoinBanner.addEventListener("click", () => {
    elements.hero.hidden = true;
  });

  elements.backToTop?.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  document.addEventListener("click", (event) => {
    const clearButton = event.target.closest(".js-clear-filters");
    if (clearButton) {
      clearFilters();
      return;
    }

    const profileLink = event.target.closest(".js-profile-link");
    if (profileLink) {
      event.stopPropagation();
      return;
    }

    const card = event.target.closest(".player-card");
    if (card) {
      togglePlayerCard(card);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.closest(".js-profile-link")) {
      return;
    }

    const card = event.target.closest(".player-card");
    if (!card) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      togglePlayerCard(card);
    }
  });

  const mediaQuery = window.matchMedia(MOBILE_MEDIA_QUERY);
  const handleMediaChange = () => applyResponsiveAria();

  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", handleMediaChange);
  } else if (typeof mediaQuery.addListener === "function") {
    mediaQuery.addListener(handleMediaChange);
  }

  window.addEventListener("scroll", updateBackToTopVisibility, { passive: true });
}

async function hydrateVisitCounter() {
  elements.visitBadge.textContent = "...";

  try {
    const count = await incrementVisitCounter();
    elements.visitBadge.textContent = Number.isFinite(count)
      ? count.toLocaleString("pt-BR")
      : "indisponivel";
  } catch (error) {
    console.error(error);
    elements.visitBadge.textContent = "indisponivel";
  }
}

function bootstrap() {
  restoreState();
  setSource(state.source, { persist: true, resetPage: false });
  bindEvents();
  updateBackToTopVisibility();
  hydrateVisitCounter();
  loadData({ resetPage: false });
}

bootstrap();
