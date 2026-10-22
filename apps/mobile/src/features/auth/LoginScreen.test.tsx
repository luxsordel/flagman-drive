import { fireEvent, render, screen } from '@testing-library/react-native';
import i18next from 'i18next';

import { initI18n } from '../../i18n';
import { LoginScreen } from './LoginScreen';

initI18n('ru');

describe('LoginScreen', () => {
  afterEach(async () => {
    await i18next.changeLanguage('ru');
  });

  it('keeps the button disabled until the number is a Belarusian mobile', async () => {
    await render(<LoginScreen />);
    const button = screen.getByRole('button', { name: 'Получить код' });
    expect(button).toBeDisabled();

    await fireEvent.changeText(screen.getByTestId('login-phone'), '17 123 45 67');
    expect(button).toBeDisabled();

    await fireEvent.changeText(screen.getByTestId('login-phone'), '29 123 45 67');
    expect(button).toBeEnabled();
  });

  it('explains that SMS sign-in is not available yet instead of calling the API', async () => {
    await render(<LoginScreen />);
    await fireEvent.changeText(screen.getByTestId('login-phone'), '291234567');
    await fireEvent.press(screen.getByRole('button', { name: 'Получить код' }));

    expect(screen.getByText('Вход по SMS появится в следующей версии пилота')).toBeOnTheScreen();
  });

  it('renders in English', async () => {
    await i18next.changeLanguage('en');
    await render(<LoginScreen />);

    expect(screen.getByRole('header', { name: 'Sign in' })).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Get code' })).toBeOnTheScreen();
  });
});
