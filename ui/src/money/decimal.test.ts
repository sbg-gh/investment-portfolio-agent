import { describe, expect, it } from 'vitest';
import { compareDecimal, formatBps, formatGrouped, isDecimalString, ratioBps, sumDecimals } from './decimal';

describe('isDecimalString', () => {
  it('accepts plain non-negative decimal strings only', () => {
    for (const ok of ['0', '12', '12.5', '0.000000000001']) expect(isDecimalString(ok)).toBe(true);
    for (const bad of ['', '-1', '1e3', '1.', '.5', ' 1', '1,000', 'NaN', '１２']) {
      expect(isDecimalString(bad)).toBe(false);
    }
  });
});

describe('arithmetic is exact', () => {
  it('sums values that floats get wrong', () => {
    expect(sumDecimals(['0.1', '0.2'])).toBe('0.3');
    expect(sumDecimals(['9007199254740993', '1'])).toBe('9007199254740994');
    expect(sumDecimals([])).toBe('0');
  });
  it('compares by value, not text', () => {
    expect(compareDecimal('1.50', '1.5')).toBe(0);
    expect(compareDecimal('2', '10')).toBe(-1);
    expect(compareDecimal('10.01', '10.001')).toBe(1);
  });
  it('rejects malformed input instead of guessing', () => {
    expect(() => sumDecimals(['1', 'abc'])).toThrow();
    expect(() => compareDecimal('-1', '1')).toThrow();
  });
});

describe('ratioBps', () => {
  it('floors to integer basis points with BigInt division', () => {
    expect(ratioBps('1', '3')).toBe(3333n);
    expect(ratioBps('2', '3')).toBe(6666n);
    expect(ratioBps('50', '100')).toBe(5000n);
    expect(ratioBps('100', '100')).toBe(10000n);
  });
  it('is zero for a zero total', () => {
    expect(ratioBps('5', '0')).toBe(0n);
  });
  it('handles large 19-digit values exactly', () => {
    expect(ratioBps('1234567890123456789.5', '2469135780246913579')).toBe(5000n);
  });
});

describe('formatting', () => {
  it('groups the integer part and keeps every fractional digit', () => {
    expect(formatGrouped('1234567.891')).toBe('1,234,567.891');
    expect(formatGrouped('999')).toBe('999');
    expect(formatGrouped('1000')).toBe('1,000');
  });
  it('formats basis points', () => {
    expect(formatBps(3333n)).toBe('33.33%');
    expect(formatBps(10000n)).toBe('100.00%');
    expect(formatBps(5n)).toBe('0.05%');
  });
});
