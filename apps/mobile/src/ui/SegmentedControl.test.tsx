import { fireEvent, render, screen } from '@testing-library/react-native';

import { SegmentedControl } from './SegmentedControl';

const options = [
  { value: 'ru', label: 'Русский' },
  { value: 'en', label: 'English', language: 'en' },
] as const;

async function renderControl(disabled = false) {
  const onChange = jest.fn();
  await render(
    <SegmentedControl
      label="Language"
      options={options}
      value="ru"
      onChange={onChange}
      disabled={disabled}
    />,
  );
  return onChange;
}

describe('SegmentedControl', () => {
  it('marks the chosen option and reports another one', async () => {
    const onChange = await renderControl();

    expect(screen.getByRole('radio', { name: 'Русский' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'English' })).not.toBeChecked();
    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    expect(onChange).toHaveBeenCalledWith('en');
  });

  it('ignores a press on the chosen option', async () => {
    const onChange = await renderControl();

    await fireEvent.press(screen.getByRole('radio', { name: 'Русский' }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('ignores presses while disabled', async () => {
    const onChange = await renderControl(true);

    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    expect(screen.getByRole('radio', { name: 'English' })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('tells the screen reader the language of a label', async () => {
    await renderControl();

    expect(screen.getByRole('radio', { name: 'English' })).toHaveProp(
      'accessibilityLanguage',
      'en',
    );
  });
});
