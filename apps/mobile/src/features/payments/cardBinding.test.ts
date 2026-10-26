import { bindCard, bindingPolls } from './cardBinding';
import { binding, fakeBindingDeps, ok, paymentCard, started } from './testing';

describe('bindCard', () => {
  it('opens the provider page and returns the bound card', async () => {
    const deps = fakeBindingDeps();
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('succeeded', paymentCard())));

    await expect(bindCard(deps)).resolves.toEqual({ kind: 'succeeded', card: paymentCard() });
    expect(deps.api.startBinding).toHaveBeenCalledWith('flagmandrive://payment-methods');
    expect(deps.openBrowser).toHaveBeenCalledWith(
      started.page_url,
      'flagmandrive://payment-methods',
    );
    expect(deps.api.getBinding).toHaveBeenCalledWith(started.id);
  });

  it('waits for the provider to report after the client came back', async () => {
    const deps = fakeBindingDeps();
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding
      .mockResolvedValueOnce(ok(binding('pending')))
      .mockResolvedValueOnce(ok(binding('pending')))
      .mockResolvedValue(ok(binding('declined')));

    await expect(bindCard(deps)).resolves.toEqual({ kind: 'declined' });
    expect(deps.wait).toHaveBeenCalledTimes(2);
  });

  it('gives up waiting and says the card may show up later', async () => {
    const deps = fakeBindingDeps();
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('pending')));

    await expect(bindCard(deps)).resolves.toEqual({ kind: 'pending' });
    expect(deps.api.getBinding).toHaveBeenCalledTimes(bindingPolls);
  });

  it('takes a page closed without a card as cancelled', async () => {
    const deps = fakeBindingDeps('closed');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('pending')));

    await expect(bindCard(deps)).resolves.toEqual({ kind: 'cancelled' });
    expect(deps.wait).not.toHaveBeenCalled();
  });

  it('still reports a card bound before the page was closed', async () => {
    const deps = fakeBindingDeps('closed');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('succeeded', paymentCard())));

    await expect(bindCard(deps)).resolves.toEqual({ kind: 'succeeded', card: paymentCard() });
  });

  it.each(['cancelled', 'expired'] as const)('reports a %s binding', async (status) => {
    const deps = fakeBindingDeps();
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding(status)));

    await expect(bindCard(deps)).resolves.toEqual({ kind: status });
  });

  it('does not open the page when the API refuses to start', async () => {
    const deps = fakeBindingDeps();
    deps.api.startBinding.mockResolvedValue({
      ok: false,
      error: { kind: 'rateLimited', retryAfterSeconds: 600 },
    });

    await expect(bindCard(deps)).resolves.toEqual({
      kind: 'error',
      error: { kind: 'rateLimited', retryAfterSeconds: 600 },
    });
    expect(deps.openBrowser).not.toHaveBeenCalled();
  });
});
