import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { DebtBanner } from './DebtBanner';
import { fine } from './testing';

initI18n('ru');

describe('DebtBanner', () => {
  it('sums the debts and opens the first one', async () => {
    const onOpen = jest.fn();
    await render(
      <DebtBanner
        debts={[
          { rental_id: 'r-1', amount: 300, ended_at: null },
          { rental_id: 'r-2', amount: 150, ended_at: null },
        ]}
        unpaidFines={[fine({ status: 'unpaid' })]}
        onOpen={onOpen}
        onOpenFines={jest.fn()}
      />,
    );

    // The rental's debt is paid in its receipt first; the fines wait for it.
    expect(screen.getByRole('alert')).toHaveTextContent(/^Долг за аренду: 4,50\sBYN$/);
    await fireEvent.press(screen.getByRole('button', { name: 'Оплатить' }));
    expect(onOpen).toHaveBeenCalledWith('r-1');
  });

  it('sends to the fines when only a fine is unpaid', async () => {
    const onOpenFines = jest.fn();
    await render(
      <DebtBanner
        debts={[]}
        unpaidFines={[fine({ status: 'unpaid', amount: 5000 })]}
        onOpen={jest.fn()}
        onOpenFines={onOpenFines}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(/^Неоплаченный штраф: 50,00\sBYN$/);
    await fireEvent.press(screen.getByRole('button', { name: 'Оплатить' }));
    expect(onOpenFines).toHaveBeenCalled();
  });

  it('shows nothing without debts', async () => {
    await render(
      <DebtBanner debts={[]} unpaidFines={[]} onOpen={jest.fn()} onOpenFines={jest.fn()} />,
    );

    expect(screen.queryByRole('alert')).toBeNull();
  });
});
