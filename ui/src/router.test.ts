import { describe, expect, it } from 'vitest';
import { parseRoute, hrefFor } from './router';

describe('parseRoute', () => {
  it.each([
    ['', { name: 'portfolios' }],
    ['#/', { name: 'portfolios' }],
    ['#/login', { name: 'login' }],
    ['#/portfolios', { name: 'portfolios' }],
    ['#/portfolios/p-1', { name: 'holdings', id: 'p-1' }],
    ['#/portfolios/p-1/valuation', { name: 'valuation', id: 'p-1' }],
    ['#/chat', { name: 'chat' }],
    ['#/operator', { name: 'operator' }],
  ])('maps %s', (hash, expected) => {
    expect(parseRoute(hash)).toEqual(expected);
  });
  it.each(['#/nope', '#/portfolios/../x', '#/portfolios/a%2Fb', '#/portfolios/p/valuation/extra', '#/portfolios/<x>'])(
    'treats %s as not found',
    (hash) => {
      expect(parseRoute(hash)).toEqual({ name: 'not-found' });
    },
  );
  it('builds hrefs that round-trip', () => {
    expect(parseRoute(hrefFor({ name: 'valuation', id: 'abc' }))).toEqual({ name: 'valuation', id: 'abc' });
  });
  it('carries a validated symbol on the operator route and round-trips it', () => {
    expect(parseRoute('#/operator?symbol=AMD')).toEqual({ name: 'operator', symbol: 'AMD' });
    expect(parseRoute('#/operator?symbol=BRK.B')).toEqual({ name: 'operator', symbol: 'BRK.B' });
    expect(parseRoute(hrefFor({ name: 'operator', symbol: 'AMD' }))).toEqual({ name: 'operator', symbol: 'AMD' });
    expect(hrefFor({ name: 'operator' })).toBe('#/operator');
  });
  it('drops an invalid or extra operator query instead of trusting it', () => {
    expect(parseRoute('#/operator?symbol=<x>')).toEqual({ name: 'operator' });
    expect(parseRoute('#/operator?symbol=amd')).toEqual({ name: 'operator' });
    expect(parseRoute('#/operator?other=1')).toEqual({ name: 'operator' });
    expect(parseRoute('#/portfolios?symbol=AMD')).toEqual({ name: 'portfolios' });
  });
});
