// Wire types. Money and quantities are decimal STRINGS everywhere.
export type Role = 'user' | 'operator';
export type SessionInfo = { user_id: string; role: Role; csrf: string };
export type Portfolio = { id: string; name: string };
export type Holding = { security_id: string; symbol: string | null; kind: string | null; quantity: string };
export type Receipt = {
  id: string;
  security_id: string;
  source: string;
  market_as_of: string;
  quantity: string;
  action: 'upsert' | 'remove';
};
export type LineStatus = 'fresh' | 'stale' | 'missing';
export type ValuationLine = {
  security_id: string;
  kind: string;
  quantity: string;
  basis: 'price' | 'nav';
  status: LineStatus;
  market_value: string | null;
  unit_value: string | null;
  observation_id: string | null;
  source: string | null;
  observed_at: string | null;
  fetched_at: string | null;
};
export type Valuation = {
  snapshot_id: string;
  currency: string;
  market_as_of: string;
  source: string;
  policy: Record<string, number>;
  status: 'complete' | 'stale' | 'incomplete';
  total: string | null;
  priced_subtotal: string;
  lines: ValuationLine[];
};
export type Security = { security_id: string; symbol: string; kind: string };
export type ChatRequest = { message: string; portfolio_id?: string; conversation_id?: string };
export type EvidenceRow = {
  security_id: string;
  status: string;
  market_value: string | null;
  observed_at: string | null;
  as_of: string;
};
export type ChatEvent =
  | { event: 'status'; data: { state: string } }
  | { event: 'evidence'; data: EvidenceRow[] }
  | { event: 'explanation'; data: { text: string } }
  | { event: 'proposals'; data: unknown[] }
  | { event: 'done'; data: { model_status: 'ok' | 'unavailable'; conversation_id: string; request_id: string } }
  | { event: 'error'; data: { error: string; request_id?: string } };

export type StarterInstrumentsResult = { registered: number; already_present: number; symbols: number };
export type SamplePortfolioResult = { id: string; name: string; created: boolean };
