/** What the card tells the queue after a decision, so the operator sees it went through. */
export interface KycQueueState {
  decided?: 'approved' | 'rejected';
}
