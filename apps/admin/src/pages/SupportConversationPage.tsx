import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import { SupportPhoto } from '../support/SupportPhoto';
import type { Conversation, StaffMessage } from '../support/api';
import { chatPollMs, useInterval } from '../support/useInterval';
import { formatMinskTime } from '../time/minsk';

type ActionProblem = 'closed' | 'taken' | 'rateLimited' | 'failed';

function actionProblem(error: unknown): ActionProblem {
  if (error instanceof ApiError) {
    if (error.problem === 'conversation-closed') return 'closed';
    if (error.problem === 'conversation-taken') return 'taken';
    if (error.status === 429) return 'rateLimited';
  }
  return 'failed';
}

/** One conversation: new messages are polled, as ADR-0023 chose. */
export function SupportConversationPage() {
  const { t, i18n } = useTranslation();
  const { conversationId = '' } = useParams();
  const { support } = useApis();
  const { signOut } = useAuth();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<StaffMessage[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ActionProblem | null>(null);
  // Read by the poll, which must not restart with every message.
  const lastSeq = useRef(0);

  const append = useCallback((incoming: StaffMessage[]) => {
    setMessages((known) => {
      const seen = new Set(known.map((message) => message.id));
      return [...known, ...incoming.filter((message) => !seen.has(message.id))];
    });
    for (const message of incoming) lastSeq.current = Math.max(lastSeq.current, message.seq);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [card, page] = await Promise.all([
        support.conversation(conversationId),
        support.messages(conversationId, lastSeq.current),
      ]);
      setConversation(card);
      append(page.items);
      setLoadFailed(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setLoadFailed(true);
    }
  }, [support, conversationId, append, signOut]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useInterval(() => {
    void refresh();
  }, chatPollMs);

  const act = async (action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setProblem(null);
    try {
      await action();
      await refresh();
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setProblem(actionProblem(error));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const send = (event: SyntheticEvent) => {
    event.preventDefault();
    const answer = text.trim();
    if (!answer) return;
    void act(async () => {
      append([await support.reply(conversationId, answer, lastSeq.current)]);
    }).then((sent) => {
      if (sent) setText('');
    });
  };

  if (!conversation) {
    return loadFailed ? (
      <p role="alert" className="list-error">
        {t('support.failed')}
      </p>
    ) : (
      <p role="status" className="muted">
        {t('support.loading')}
      </p>
    );
  }

  const open = conversation.status === 'open';
  return (
    <>
      <h1>{fullName(conversation) ?? t('support.noName')}</h1>
      <dl className="details support-details">
        <dt>{t('support.columns.car')}</dt>
        <dd>{conversation.car_plate ?? t('support.noRental')}</dd>
        {conversation.rental_status && (
          <>
            <dt>{t('support.rental')}</dt>
            <dd>{t(`support.rentalStatus.${conversation.rental_status}`)}</dd>
          </>
        )}
        <dt>{t('support.columns.operator')}</dt>
        <dd>{conversation.assigned_staff_name ?? t('support.nobody')}</dd>
      </dl>
      {loadFailed && (
        <p role="alert" className="list-error">
          {t('support.stale')}
        </p>
      )}
      <ol className="support-chat" aria-label={t('support.messages')}>
        {messages.map((message) => (
          <li
            key={message.id}
            className={
              message.author === 'staff'
                ? 'support-message support-message-staff'
                : 'support-message'
            }
          >
            <p className="muted">
              {message.author === 'staff'
                ? (message.staff_name ?? t('support.operator'))
                : t('support.client')}
              {' · '}
              <time dateTime={message.created_at}>
                {formatMinskTime(message.created_at, i18n.language)}
              </time>
            </p>
            {message.kind === 'photo' ? (
              <SupportPhoto load={() => support.messagePhotoUrl(conversationId, message.id)} />
            ) : (
              <p>{message.text}</p>
            )}
          </li>
        ))}
      </ol>
      {problem && (
        <p role="alert" className="list-error">
          {t(`support.problems.${problem}`)}
        </p>
      )}
      {open ? (
        <form className="decision-form" onSubmit={send}>
          <label className="stacked-label">
            {t('support.answer')}
            <textarea
              className="field textarea"
              rows={3}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
          </label>
          <div className="support-actions">
            <button type="submit" className="button button-primary" disabled={busy || !text.trim()}>
              {t('support.send')}
            </button>
            {!conversation.assigned_staff_id && (
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => void act(() => support.take(conversationId))}
              >
                {t('support.take')}
              </button>
            )}
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void act(() => support.close(conversationId))}
            >
              {t('support.close')}
            </button>
          </div>
        </form>
      ) : (
        <p className="muted">{t('support.closed')}</p>
      )}
    </>
  );
}
