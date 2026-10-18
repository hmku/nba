/** Draft order helpers. Order types: 'snake' and 'snake_3rr' (third-round reversal).
 *  snake:      R1 1→N, R2 N→1, R3 1→N, ...
 *  snake_3rr:  R1 1→N, R2 N→1, R3 N→1, R4 1→N, R5 N→1, ...
 */
export function isReversed(round: number, orderType: string): boolean {
  if (orderType === "snake_3rr") {
    if (round === 1) return false;
    if (round === 2 || round === 3) return true;
    return round % 2 === 1;
  }
  if (orderType === "linear") return false;
  return round % 2 === 0;
}

export function pickInfo(overall: number, numTeams: number, orderType: string) {
  const round = Math.floor((overall - 1) / numTeams) + 1;
  const idx = (overall - 1) % numTeams;
  const slot = isReversed(round, orderType) ? numTeams - idx : idx + 1;
  return { round, slot, pickInRound: idx + 1 };
}

export function overallFor(round: number, slot: number, numTeams: number, orderType: string) {
  const idx = isReversed(round, orderType) ? numTeams - slot : slot - 1;
  return (round - 1) * numTeams + idx + 1;
}

export function picksForSlot(slot: number, numTeams: number, numRounds: number, orderType: string) {
  const out: number[] = [];
  for (let r = 1; r <= numRounds; r++) out.push(overallFor(r, slot, numTeams, orderType));
  return out;
}
