import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { DebtBanner } from './DebtBanner';

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
        onOpen={onOpen}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(/^Долг за аренду: 4,50\sBYN$/);
    await fireEvent.press(screen.getByRole('button', { name: 'Оплатить' }));
    expect(onOpen).toHaveBeenCalledWith('r-1');
  });

  it('shows nothing without debts', async () => {
    await render(<DebtBanner debts={[]} onOpen={jest.fn()} />);

    expect(screen.queryByRole('alert')).toBeNull();
  });
});
