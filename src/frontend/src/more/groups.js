// On a phone, Account and Admin are reached from the More tab, one screen per group of sections. ``sections`` are
// the ids of the <Section>s each screen shows.
export const ACCOUNT_GROUPS = [
  { id: 'profile', label: 'Profile', icon: 'account', sections: ['profile'] },
  { id: 'security', label: 'Security', icon: 'lock', sections: ['security'] },
  { id: 'notifications', label: 'Notifications', icon: 'bell', sections: ['notifications'] },
  { id: 'sessions', label: 'Sessions and tokens', icon: 'key', sections: ['sessions', 'tokens'] },
  { id: 'display', label: 'Display', icon: 'eye', sections: ['display'] },
]

// The Admin page on a phone starts from a list (servers, then the instance settings); each row opens one of these.
export const ADMIN_GROUPS = [
  { id: 'servers', label: 'Servers', sections: ['servers'] },
  { id: 'connect', label: 'Connect a server', sections: ['connect'] },
  { id: 'users', label: 'Users', sections: ['users'] },
  { id: 'rules', label: 'Alert rules', sections: ['rules'] },
  { id: 'agent', label: 'Incident agent', sections: ['agent'] },
  { id: 'retention', label: 'Data and retention', sections: ['retention'] },
  { id: 'about', label: 'About', sections: ['about'] },
]
