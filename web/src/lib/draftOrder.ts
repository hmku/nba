/** Draft order helpers (mirror of src/fantasy/draft/order.py).
 *  snake:      R1 1→N, R2 N→1, R3 1→N, ...
 *  snake_3rr:  R1 1→N, R2 N→1, R3 N→1, R4 1→N, R5 N→1, ...
 *  custom:     drafts.config.custom_rounds = [[slot order of round 1], [round 2], ...]
 *              (Yahoo has no native 3RR; set via `fantasy draft set-order`).
 */
export type CustomRounds = number[][] | null | undefined;

export interface PickSlot {
  overall: number;
  round: number;
  slot: number;
  pickInRound: number;
}

export function isReversed(round: number, orderType: string): boolean {
  if (orderType === "snake_3rr") {
    if (round === 1) return false;
    if (round === 2 || round === 3) return true;
    return round % 2 === 1;
  }
  if (orderType === "linear") return false;
  return round % 2 === 0;
}

/** Valid custom_rounds from a drafts.config, else null. */
export function customRoundsOf(config: unknown, numTeams: number, numRounds: number): number[][] | null {
  const cr = (config as { custom_rounds?: unknown } | null)?.custom_rounds;
  if (!Array.isArray(cr) || cr.length !== numRounds) return null;
  for (const r of cr) {
    if (!Array.isArray(r) || r.length !== numTeams) return null;
    const sorted = [...r].map(Number).sort((a, b) => a - b);
    if (sorted.some((v, i) => v !== i + 1)) return null;
  }
  return cr.map((r: unknown[]) => r.map(Number));
}

const cache = new Map<string, PickSlot[]>();

export function draftOrder(numTeams: number, numRounds: number, orderType: string, custom?: CustomRounds): PickSlot[] {
  const key = `${numTeams}|${numRounds}|${orderType}|${custom ? JSON.stringify(custom) : ""}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const out: PickSlot[] = [];
  for (let r = 1; r <= numRounds; r++) {
    const slots = custom?.[r - 1] ??
      (isReversed(r, orderType)
        ? Array.from({ length: numTeams }, (_, i) => numTeams - i)
        : Array.from({ length: numTeams }, (_, i) => i + 1));
    slots.forEach((s, i) => out.push({ overall: out.length + 1, round: r, slot: s, pickInRound: i + 1 }));
  }
  cache.set(key, out);
  return out;
}

export function pickInfo(overall: number, numTeams: number, orderType: string, custom?: CustomRounds, numRounds?: number) {
  const rounds = numRounds ?? Math.max(custom?.length ?? 0, Math.ceil(overall / numTeams));
  const order = draftOrder(numTeams, rounds, orderType, custom);
  const p = order[overall - 1];
  if (p) return p;
  // beyond the configured rounds: fall back to the formula
  const round = Math.floor((overall - 1) / numTeams) + 1;
  const idx = (overall - 1) % numTeams;
  return { overall, round, slot: isReversed(round, orderType) ? numTeams - idx : idx + 1, pickInRound: idx + 1 };
}

export function picksForSlot(slot: number, numTeams: number, numRounds: number, orderType: string, custom?: CustomRounds) {
  return draftOrder(numTeams, numRounds, orderType, custom)
    .filter((p) => p.slot === slot)
    .map((p) => p.overall);
}
