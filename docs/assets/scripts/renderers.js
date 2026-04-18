import {
  escapeHtml,
  formatDiff,
  formatNumber,
  formatSeenCompact,
  formatSeenTitle,
  getArrowClass,
  getCountryRankMuted,
  getCountryRankText,
  getDiffClass,
  getPlayerHonorBadge,
  getPeakRating,
  getPlayerName,
  getPlayerTitle,
  getProfileUrl,
  normalizeArrow,
  safeNumber
} from "./utils.js";

function renderPositionBadge(player) {
  const arrow = normalizeArrow(player?.position_arrow);
  const change = Math.abs(safeNumber(player?.position_change, 0));

  if (!arrow && !change) {
    return '<span class="pos-arrow pos-same">Novo</span>';
  }

  if (!arrow) {
    return escapeHtml(String(change));
  }

  return `<span class="pos-arrow ${getArrowClass(arrow)}">${escapeHtml(arrow)}</span>${change ? ` ${change}` : ""}`;
}

function renderIdentityChips(player) {
  const parts = [];
  const honorBadge = getPlayerHonorBadge(player);
  const title = getPlayerTitle(player);

  if (honorBadge) {
    const tooltip = honorBadge.tooltip
      ? ` title="${escapeHtml(honorBadge.tooltip)}"`
      : "";
    parts.push(
      `<span class="title-chip title-chip--champion"${tooltip}>${escapeHtml(honorBadge.label)}</span>`
    );
  }

  if (title) {
    parts.push(`<span class="title-chip">${escapeHtml(title)}</span>`);
  }

  return parts.join("");
}

function renderCurrentMetricCard(player, rhythm, sourceId, label) {
  const value = formatNumber(player?.[rhythm]);
  const diff = formatDiff(player?.[`${rhythm}_diff`]);
  const diffClass = getDiffClass(player?.[`${rhythm}_diff`]);
  const countryRank = getCountryRankText(player, rhythm, sourceId);
  const metaClass = getCountryRankMuted(player, rhythm, sourceId)
    ? "metric-meta country-rank--muted"
    : "metric-meta";

  return `
    <div class="metric-card">
      <span class="metric-label">${escapeHtml(label)}</span>
      <span class="metric-value">${escapeHtml(value)}</span>
      <span class="${diffClass}">${escapeHtml(diff)}</span>
      ${countryRank ? `<span class="${metaClass}">${escapeHtml(countryRank)}</span>` : ""}
    </div>
  `;
}

function renderPeakMetricCard(player, rhythm, label) {
  const peakValue = formatNumber(getPeakRating(player, rhythm));
  const currentValue = formatNumber(player?.[rhythm]);

  return `
    <div class="metric-card metric-card-peak">
      <span class="metric-label">Maior ${escapeHtml(label)}</span>
      <span class="metric-value">${escapeHtml(peakValue)}</span>
      <span class="metric-meta">Atual: ${escapeHtml(currentValue)}</span>
    </div>
  `;
}

function renderRatingCell(player, rhythm, sourceId) {
  const value = formatNumber(player?.[rhythm]);
  const diff = player?.[`${rhythm}_diff`];
  const countryRank = getCountryRankText(player, rhythm, sourceId);
  const countryRankClass = getCountryRankMuted(player, rhythm, sourceId)
    ? "country-rank country-rank--muted"
    : "country-rank";

  return `
    <td class="rating-cell">
      <span class="rating-value">${escapeHtml(value)}</span>
      <span class="${getDiffClass(diff)}">${escapeHtml(formatDiff(diff))}</span>
      ${countryRank ? `<span class="${countryRankClass}">${escapeHtml(countryRank)}</span>` : ""}
    </td>
  `;
}

export function renderDesktopRows(items, startIndex, sourceId, sourceConfig) {
  return items
    .map((player, index) => {
      const rank = startIndex + index + 1;
      const profileUrl = getProfileUrl(player, sourceConfig);
      const realName = getPlayerName(player);
      const identityChips = renderIdentityChips(player);
      const rowClass = rank <= 3 ? `row-top-${rank}` : "";
      const seenShort = formatSeenCompact(player?.seenAt);
      const seenTitle = formatSeenTitle(player?.seenAt);

      return `
        <tr class="${rowClass}">
          <td class="rank">#${rank}</td>
          <td class="pos-col">${renderPositionBadge(player)}</td>
          <td class="user-cell">
            <div class="user-main">
              ${identityChips}
              <a class="player-name" href="${escapeHtml(profileUrl)}" target="_blank" rel="noopener">${escapeHtml(player?.username || "sem-usuário")}</a>
            </div>
            <span class="realname">${escapeHtml(realName)}</span>
          </td>
          ${renderRatingCell(player, "blitz", sourceId)}
          ${renderRatingCell(player, "bullet", sourceId)}
          ${renderRatingCell(player, "rapid", sourceId)}
          <td class="seen-cell" title="${escapeHtml(seenTitle)}">${escapeHtml(seenShort)}</td>
        </tr>
      `;
    })
    .join("");
}

export function renderMobileCards(items, startIndex, sourceId, sourceConfig) {
  return items
    .map((player, index) => {
      const rank = startIndex + index + 1;
      const profileUrl = getProfileUrl(player, sourceConfig);
      const realName = getPlayerName(player);
      const identityChips = renderIdentityChips(player);
      const positionBadge = renderPositionBadge(player);
      const rowClass = rank <= 3 ? `row-top-${rank}` : "";
      const initial = escapeHtml(String(player?.username || "?").charAt(0).toUpperCase() || "?");
      const seenShort = formatSeenCompact(player?.seenAt);
      const seenTitle = formatSeenTitle(player?.seenAt);

      return `
        <article class="player-card ${rowClass}" tabindex="0" role="button" aria-pressed="false" aria-label="Mostrar máximas de ${escapeHtml(player?.username || "sem-usuário")}">
          <div class="player-card-inner">
            <section class="player-card-face player-card-face-front">
              <div class="player-card-header">
                <div class="avatar" aria-hidden="true">${initial}</div>
                <div class="player-card-body">
                  <div class="player-title-row">
                    <span class="player-rank-label">#${rank}</span>
                    ${identityChips}
                    <span class="player-name">${escapeHtml(player?.username || "sem-usuário")}</span>
                  </div>
                  <span class="player-realname">${escapeHtml(realName)}</span>
                  <span class="player-country">Movimento: ${positionBadge}</span>
                  <span class="player-country" title="${escapeHtml(seenTitle)}">Ativo: ${escapeHtml(seenShort)}</span>
                </div>
              </div>

              <div class="player-metrics">
                ${renderCurrentMetricCard(player, "blitz", sourceId, "Blitz")}
                ${renderCurrentMetricCard(player, "bullet", sourceId, "Bullet")}
                ${renderCurrentMetricCard(player, "rapid", sourceId, "Rapid")}
              </div>

              <div class="player-card-actions">
                <span class="player-card-hint">Toque para ver máximas registradas</span>
                <a class="player-profile-link js-profile-link" href="${escapeHtml(profileUrl)}" target="_blank" rel="noopener">Perfil</a>
              </div>
            </section>

            <section class="player-card-face player-card-face-back">
              <div class="player-card-header">
                <div class="avatar" aria-hidden="true">${initial}</div>
                <div class="player-card-body">
                  <div class="player-title-row">
                    <span class="player-rank-label">#${rank}</span>
                    ${identityChips}
                    <span class="player-name">${escapeHtml(player?.username || "sem-usuário")}</span>
                  </div>
                  <span class="player-realname">${escapeHtml(realName)}</span>
                  <span class="player-country">Máximas registradas</span>
                  <span class="player-country">Clique novamente para voltar</span>
                </div>
              </div>

              <div class="player-metrics">
                ${renderPeakMetricCard(player, "blitz", "Blitz")}
                ${renderPeakMetricCard(player, "bullet", "Bullet")}
                ${renderPeakMetricCard(player, "rapid", "Rapid")}
              </div>

              <div class="player-card-actions">
                <span class="player-card-hint">Histórico de pico por ritmo</span>
                <a class="player-profile-link js-profile-link" href="${escapeHtml(profileUrl)}" target="_blank" rel="noopener">Perfil</a>
              </div>
            </section>
          </div>
        </article>
      `;
    })
    .join("");
}

export function renderEmptyState(hasFilters) {
  const title = hasFilters
    ? "Nenhum jogador encontrado com os filtros atuais."
    : "Ainda não há jogadores disponíveis nesta fonte.";
  const hint = hasFilters
    ? "Limpe os filtros para voltar ao ranking completo."
    : "Tente novamente após a próxima geração dos dados.";
  const clearButton = hasFilters
    ? '<button type="button" class="clear-btn js-clear-filters">Limpar filtros</button>'
    : "";

  return `
    <div class="empty-block">
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(hint)}</small>
      ${clearButton}
    </div>
  `;
}

function createPagerButton(label, targetPage, disabled, currentPage, onPageChange) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.disabled = disabled;

  if (targetPage === currentPage && !disabled) {
    button.classList.add("is-current");
    button.setAttribute("aria-current", "page");
  }

  button.addEventListener("click", () => {
    if (!disabled && targetPage !== currentPage) {
      onPageChange(targetPage);
    }
  });

  return button;
}

export function renderPager(container, page, totalPages, onPageChange) {
  container.innerHTML = "";

  if (totalPages <= 1) {
    return;
  }

  const firstPage = 1;
  const lastPage = totalPages;
  const start = Math.max(firstPage, page - 2);
  const end = Math.min(lastPage, page + 2);

  container.appendChild(
    createPagerButton("<<", firstPage, page === firstPage, page, onPageChange)
  );
  container.appendChild(
    createPagerButton("<", Math.max(firstPage, page - 1), page === firstPage, page, onPageChange)
  );

  for (let current = start; current <= end; current += 1) {
    container.appendChild(
      createPagerButton(String(current), current, false, page, onPageChange)
    );
  }

  container.appendChild(
    createPagerButton(">", Math.min(lastPage, page + 1), page === lastPage, page, onPageChange)
  );
  container.appendChild(
    createPagerButton(">>", lastPage, page === lastPage, page, onPageChange)
  );
}
