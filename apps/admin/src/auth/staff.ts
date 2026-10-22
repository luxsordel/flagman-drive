/** Staff roles of the identity module (#32); every staff member has exactly one. */
export const staffRoles = ['operator', 'technician', 'admin'] as const;
export type StaffRole = (typeof staffRoles)[number];

/** The signed-in staff member, as `GET /staff/me` returns it. */
export interface Staff {
  id: string;
  email: string;
  role: StaffRole;
}
