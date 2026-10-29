import * as WebBrowser from 'expo-web-browser';

import { PaymentMethodsScreen } from '../features/payments/PaymentMethodsScreen';

// On the web the provider's page runs in a popup and comes back to this route: this hands the
// address to the window that opened it and closes the popup. Elsewhere it does nothing.
WebBrowser.maybeCompleteAuthSession();

export default PaymentMethodsScreen;
