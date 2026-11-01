import { discardPhoto } from '../kyc/photos';
import type { SupportApi, SupportError, SupportMessage, SupportResult } from './supportApi';

export interface OutgoingMessage {
  /** Chosen once, so that a resend after a lost answer is not a second message. */
  id: string;
  text: string | null;
  photoUri: string | null;
  rentalId: string | null;
  /** `waiting`: goes again by itself. `refused`: the server will never take it as it is. */
  state: 'sending' | 'waiting' | 'refused';
}

export interface ChatState {
  conversationId: string | null;
  closed: boolean;
  messages: SupportMessage[];
  outgoing: OutgoingMessage[];
  loaded: boolean;
  error: SupportError | null;
}

const empty: ChatState = {
  conversationId: null,
  closed: false,
  messages: [],
  outgoing: [],
  loaded: false,
  error: null,
};

/** Neither the network nor a passing limit: sending the same thing again cannot help. */
const refused: ReadonlySet<SupportError['kind']> = new Set(['tooLarge', 'unsupported']);

function bySeq(messages: SupportMessage[]): SupportMessage[] {
  const unique = new Map(messages.map((message) => [message.id, message]));
  return [...unique.values()].sort((a, b) => a.seq - b.seq);
}

/**
 * The conversation with support and what has not reached the server yet. Lives as long as the app
 * does, so unsent messages survive leaving the screen, though not a restart.
 */
export class ChatStore {
  private state = empty;
  private readonly listeners = new Set<() => void>();
  // Only the poll moves it: a saved message of ours may overtake an operator's answer not yet seen.
  private lastSeq = 0;
  private flushing = false;

  constructor(
    private readonly api: SupportApi,
    private readonly newId: () => string,
  ) {}

  getState = (): ChatState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private update(change: Partial<ChatState>) {
    this.state = { ...this.state, ...change };
    this.listeners.forEach((listener) => {
      listener();
    });
  }

  private patchOutgoing(id: string, state: OutgoingMessage['state']) {
    this.update({
      outgoing: this.state.outgoing.map((item) => (item.id === id ? { ...item, state } : item)),
    });
  }

  reset() {
    for (const item of this.state.outgoing) {
      if (item.photoUri !== null) discardPhoto(item.photoUri);
    }
    this.lastSeq = 0;
    this.state = empty;
    this.update({});
  }

  /** New messages; a working connection is also the signal to resend what waits. */
  async poll(): Promise<void> {
    const result = await this.api.messages(this.lastSeq);
    if (!result.ok) {
      this.update({ error: result.error, loaded: true });
      return;
    }
    const { conversation_id: conversationId, status, items } = result.value;
    // Support closed the old conversation and the client has a new one: its `seq` goes on, but
    // what was shown belongs to the old one.
    const switched = conversationId !== this.state.conversationId && this.state.conversationId;
    const known = switched ? [] : this.state.messages;
    this.lastSeq = items.reduce((last, item) => Math.max(last, item.seq), this.lastSeq);
    this.update({
      conversationId,
      closed: status === 'closed',
      messages: bySeq([...known, ...items]),
      loaded: true,
      error: null,
    });
    await this.flush();
  }

  send(text: string, rentalId: string | null): Promise<void> {
    return this.enqueue({ text, photoUri: null, rentalId });
  }

  sendPhoto(photoUri: string): Promise<void> {
    return this.enqueue({ text: null, photoUri, rentalId: null });
  }

  private enqueue(item: Pick<OutgoingMessage, 'text' | 'photoUri' | 'rentalId'>): Promise<void> {
    const message: OutgoingMessage = { ...item, id: this.newId(), state: 'waiting' };
    this.update({ outgoing: [...this.state.outgoing, message] });
    return this.flush();
  }

  retry(id: string): Promise<void> {
    this.patchOutgoing(id, 'waiting');
    return this.flush();
  }

  /** The client gives up on a message; its photo leaves the device. */
  remove(id: string) {
    const item = this.state.outgoing.find((candidate) => candidate.id === id);
    if (item?.photoUri) discardPhoto(item.photoUri);
    this.update({ outgoing: this.state.outgoing.filter((candidate) => candidate.id !== id) });
  }

  /** One at a time and in order: the operator reads them as the client wrote them. */
  private async flush(): Promise<void> {
    if (this.flushing) return;
    this.flushing = true;
    try {
      for (;;) {
        const next = this.state.outgoing.find((item) => item.state === 'waiting');
        if (next === undefined) return;
        this.patchOutgoing(next.id, 'sending');
        const result = await this.deliver(next);
        if (!result.ok) {
          if (refused.has(result.error.kind)) {
            this.patchOutgoing(next.id, 'refused');
            continue;
          }
          // Waits for the next poll that gets through; the order stays.
          this.patchOutgoing(next.id, 'waiting');
          this.update({ error: result.error });
          return;
        }
        if (next.photoUri !== null) discardPhoto(next.photoUri);
        this.update({
          messages: bySeq([...this.state.messages, result.value]),
          outgoing: this.state.outgoing.filter((item) => item.id !== next.id),
        });
      }
    } finally {
      this.flushing = false;
    }
  }

  private deliver(item: OutgoingMessage): Promise<SupportResult<SupportMessage>> {
    return item.photoUri !== null
      ? this.api.sendPhoto(item.photoUri, item.id)
      : this.api.sendText(item.text ?? '', item.rentalId, item.id);
  }
}
