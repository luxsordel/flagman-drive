import { lightPalette } from '@flagman/design-tokens';
import { render, screen } from '@testing-library/react-native';
import { createRef } from 'react';
import type { TextInputInstance } from 'react-native';

import { TextField } from './TextField';

describe('TextField', () => {
  it('shows the hint while the value is fine', async () => {
    await render(<TextField label="Email" hint="Optional" value="" />);

    expect(screen.getByText('Optional')).toBeOnTheScreen();
    // The visible hint is read with the text around it; repeating it on the field is noise.
    expect(screen.getByLabelText('Email')).not.toHaveProp('accessibilityHint');
  });

  it('shows the error instead of the hint and reads it on the field', async () => {
    await render(<TextField label="Email" hint="Optional" error="Check the address" value="" />);

    expect(screen.queryByText('Optional')).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('Check the address');
    expect(screen.getByLabelText('Email')).toHaveProp('accessibilityHint', 'Check the address');
    // The frame is the input's own host parent; a test-only ID on it would leak into the API.
    expect(screen.getByLabelText('Email').parent).toHaveStyle({
      borderColor: lightPalette.danger,
    });
  });

  it('keeps the error on the field over a hint passed by the caller', async () => {
    await render(
      <TextField label="Email" error="Check the address" accessibilityHint="Optional" value="" />,
    );

    expect(screen.getByLabelText('Email')).toHaveProp('accessibilityHint', 'Check the address');
  });

  it('hands the input to a ref, so a previous field can move the focus here', async () => {
    const ref = createRef<TextInputInstance>();
    await render(<TextField ref={ref} label="Email" value="" />);

    expect(typeof ref.current?.focus).toBe('function');
  });
});
