import { ApiError } from './api/client';

const GENERIC = 'Something went wrong. Please try again.';
const IMPORT_ERRORS: Record<string, string> = {
  invalid_source: 'The price list name must be 1 to 64 letters, digits, dots, dashes or underscores, starting with a letter or digit.',
  invalid_encoding: 'The file must be UTF-8 text.',
  file_too_large: 'The file is larger than 1 MiB.',
  too_many_rows: 'The file has more than 10,000 data rows.',
  empty_file: 'The file has no data rows.',
  invalid_header: 'The file needs a header row with the columns symbol, date and close, each exactly once.',
  invalid_csv: 'The file is not valid CSV.',
  timezone_unavailable: 'New York time zone data is not available on the server.',
};

/** Fixed, user-facing text per failure. Server text is never shown. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong. Please try again.';
  if (error.status === 404) return 'Not found. It may not exist or may not belong to you.';
  if (error.status === 403) return 'Operator role required.';
  if (error.status === 429) {
    return error.retryAfter === null
      ? 'Too many requests. Please wait and try again.'
      : `Too many requests. Please wait ${error.retryAfter} seconds and try again.`;
  }
  if (error.status === 503) return 'The server is busy. Please try again shortly.';
  if (error.status === 413) return 'That request was too large.';
  if (error.status === 0) return 'Could not reach the server.';
  if (error.code === 'numeric_money') return 'The server sent a number where a decimal string was required; the data was rejected.';
  if (error.status === 422 && error.code in IMPORT_ERRORS) return IMPORT_ERRORS[error.code] ?? GENERIC;
  if (error.status === 422) return 'Please check the fields and try again.';
  return 'Something went wrong. Please try again.';
}

export const DECIMAL_HELP = 'Enter a plain decimal number such as 10 or 10.5 (no signs, commas or exponents).';
