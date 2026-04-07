export const DEFAULTS = Object.freeze({
  source: "lichess",
  sort: "blitz",
  order: "desc",
  perPage: "20"
});

export const RHYTHMS = Object.freeze(["blitz", "bullet", "rapid"]);
export const SOURCE_STORAGE_KEY = "ranking_selected_source";
export const MOBILE_MEDIA_QUERY = "(max-width: 920px)";

export const VISIT_COUNTER_CONFIG = Object.freeze({
  apiBaseUrl: "https://api.counterapi.dev/v1",
  namespace: "ranking-xadrez-jovem-es",
  key: "home"
});

export const SOURCE_CONFIG = Object.freeze({
  lichess: Object.freeze({
    id: "lichess",
    label: "Lichess",
    file: "players.json",
    ctaUrl: "https://lichess.org/team/xadrezjovemes",
    ctaLabel: "Entrar no time",
    profileBase: "https://lichess.org/@/",
    heroTitle: "Quer aparecer no ranking?",
    heroDescription:
      'Seja membro do time <strong>xadrezjovemes</strong> no Lichess. Você aparece automaticamente após a geração diária.',
    footerText: "Dados: Lichess. Atualizado uma vez por dia.",
    hasCountryRanks: false
  }),
  chesscom: Object.freeze({
    id: "chesscom",
    label: "Chess.com",
    file: "players_chesscom.json",
    ctaUrl: "https://www.chess.com/club/xadrez-jovem-es",
    ctaLabel: "Entrar no clube",
    profileBase: "https://www.chess.com/member/",
    heroTitle: "Quer aparecer no ranking?",
    heroDescription:
      'Seja membro do clube <strong>xadrez-jovem-es</strong> no Chess.com. O ranking nacional aparece por ritmo quando o dado público estiver disponível.',
    footerText: "Dados: Chess.com. Atualizado uma vez por dia.",
    hasCountryRanks: true
  })
});
