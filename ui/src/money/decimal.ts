// Exact decimal-string arithmetic. Strings in, strings (or BigInt basis points) out.
// No floats, no Number coercion, no Math: ESLint enforces this inside src/money.
const DECIMAL = /^[0-9]+(\.[0-9]+)?$/;
const MAX_LENGTH = 40;

export function isDecimalString(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_LENGTH && DECIMAL.test(value);
}

type Scaled = { units: bigint; scale: number };

function parse(value: string): Scaled {
  if (!isDecimalString(value)) throw new Error('invalid decimal string');
  const [whole = '0', fraction = ''] = value.split('.');
  return { units: BigInt(whole + fraction), scale: fraction.length };
}

function rescale(parsed: Scaled, scale: number): bigint {
  return parsed.units * 10n ** BigInt(scale - parsed.scale);
}

function common(values: Scaled[]): number {
  return values.reduce((widest, item) => (item.scale > widest ? item.scale : widest), 0);
}

function render(units: bigint, scale: number): string {
  const digits = units.toString().padStart(scale + 1, '0');
  const whole = digits.slice(0, digits.length - scale);
  const fraction = digits.slice(digits.length - scale).replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : whole;
}

export function compareDecimal(a: string, b: string): -1 | 0 | 1 {
  const pa = parse(a);
  const pb = parse(b);
  const scale = common([pa, pb]);
  const ua = rescale(pa, scale);
  const ub = rescale(pb, scale);
  return ua === ub ? 0 : ua < ub ? -1 : 1;
}

export function sumDecimals(values: string[]): string {
  const parsed = values.map(parse);
  const scale = common(parsed);
  const total = parsed.reduce((sum, item) => sum + rescale(item, scale), 0n);
  return render(total, scale);
}

/** part / total in whole basis points, floored. A zero total yields 0 (nothing to allocate). */
export function ratioBps(part: string, total: string): bigint {
  const pp = parse(part);
  const pt = parse(total);
  const scale = common([pp, pt]);
  const denominator = rescale(pt, scale);
  if (denominator === 0n) return 0n;
  return (rescale(pp, scale) * 10000n) / denominator;
}

export function formatGrouped(value: string): string {
  if (!isDecimalString(value)) throw new Error('invalid decimal string');
  const [whole = '0', fraction] = value.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction === undefined ? grouped : `${grouped}.${fraction}`;
}

export function formatBps(bps: bigint): string {
  const whole = bps / 100n;
  const cents = (bps % 100n).toString().padStart(2, '0');
  return `${whole.toString()}.${cents}%`;
}
