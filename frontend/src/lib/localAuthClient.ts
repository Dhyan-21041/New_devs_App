// Local authentication client to replace Supabase
import { decodeJWTPayload } from '../utils/jwtUtils';

interface AuthUser {
  id: string;
  email: string;
  name?: string;
  is_admin?: boolean;
  tenant_id?: string;
  app_metadata?: any;
  user_metadata?: any;
  created_at?: string;
}

interface AuthSession {
  access_token: string;
  refresh_token?: string;
  user: AuthUser;
  token_type: string;
  expires_in?: number;
  expires_at?: number;
}

interface AuthResponse {
  data: { user: AuthUser | null; session: AuthSession | null };
  user: AuthUser | null;
  session: AuthSession | null;
  error: Error | null;
}

interface SignInCredentials {
  email: string;
  password: string;
}

class LocalAuthClient {
  private subscribers: ((event: string, session: AuthSession | null) => void)[] = [];
  private session: AuthSession | null = null;
  private storageKey = 'base360-auth-token';

  constructor() {
    this.loadSession();
  }

  private getApiUrl(): string {
    return import.meta.env.VITE_API_URL || import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';
  }

  private notifySubscribers(event: string, session: AuthSession | null) {
    console.log(`📣 [LocalAuth] Notifying ${this.subscribers.length} subscribers of event: ${event}`);
    this.subscribers.forEach((callback) => {
      try {
        callback(event, session);
      } catch (e) {
        console.error('📣 [LocalAuth] Error in subscriber callback:', e);
      }
    });
  }

  private saveSession(session: AuthSession | null) {
    this.session = session;
    if (session) {
      localStorage.setItem(this.storageKey, JSON.stringify(session));
      this.notifySubscribers('SIGNED_IN', session);
    } else {
      localStorage.removeItem(this.storageKey);
      this.notifySubscribers('SIGNED_OUT', null);
    }
  }

  private loadSession() {
    try {
      const stored = localStorage.getItem(this.storageKey);
      if (stored) {
        this.session = JSON.parse(stored);
        if (this.session) {
          this.session.expires_at = decodeJWTPayload(this.session.access_token)?.exp;
        }
      }
    } catch (error) {
      console.warn('[LocalAuth] Failed to load session from storage:', error);
      localStorage.removeItem(this.storageKey);
    }
  }

  async signInWithPassword(credentials: SignInCredentials): Promise<AuthResponse> {
    try {
      const response = await fetch(`${this.getApiUrl()}/api/v1/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(credentials),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      }

      if (data.error) {
        throw new Error(data.error);
      }

      const session: AuthSession = {
        access_token: data.access_token,
        token_type: data.token_type || 'bearer',
        expires_at: decodeJWTPayload(data.access_token)?.exp,
        user: {
          ...data.user,
          app_metadata: { ...data.user.app_metadata, tenant_id: data.user.tenant_id },
        },
      };

      this.saveSession(session);

      return {
        data: { user: session.user, session },
        user: data.user,
        session: session,
        error: null,
      };
    } catch (error: any) {
      console.error('[LocalAuth] Sign in failed:', error);
      return {
        data: { user: null, session: null },
        user: null,
        session: null,
        error: error,
      };
    }
  }

  async signOut(): Promise<{ error: Error | null }> {
    try {
      // Call backend logout endpoint if needed
      if (this.session?.access_token) {
        try {
          await fetch(`${this.getApiUrl()}/api/v1/auth/logout`, {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${this.session.access_token}`,
            },
          });
        } catch (logoutError) {
          console.warn('[LocalAuth] Backend logout failed (continuing):', logoutError);
        }
      }

      this.saveSession(null);

      return { error: null };
    } catch (error: any) {
      console.error('[LocalAuth] Sign out failed:', error);
      return { error: error };
    }
  }

  async getSession(): Promise<{ data: { session: AuthSession | null }; error: Error | null }> {
    const session = this.session;
    // Check if current session is still valid
    if (session?.access_token) {
      try {
        // Verify token is still valid by calling a protected endpoint
        const response = await fetch(`${this.getApiUrl()}/api/v1/auth/me`, {
          headers: {
            'Authorization': `Bearer ${session.access_token}`,
          },
        });

        if (this.session !== session) {
          return { data: { session: this.session }, error: null };
        }
        if (response.status === 401) {
          // Session invalid, clear it
          this.saveSession(null);
        }
      } catch (error) {
        console.warn('[LocalAuth] Session validation failed:', error);
        // A temporary outage is not evidence that the token was revoked.
      }
    }

    return { data: { session: this.session }, error: null };
  }

  // Challenge JWTs have a fixed lifetime and no refresh-token endpoint.
  // Revalidate the existing token instead of pretending to issue a new one.
  async refreshSession() {
    return this.getSession();
  }

  async getUser(token?: string): Promise<{ data: { user: AuthUser | null }; user: AuthUser | null; error: Error | null }> {
    const tokenToUse = token || this.session?.access_token;

    if (!tokenToUse) {
      return { data: { user: null }, user: null, error: null };
    }

    try {
      const response = await fetch(`${this.getApiUrl()}/api/v1/auth/me`, {
        headers: {
          'Authorization': `Bearer ${tokenToUse}`,
        },
      });

      if (response.ok) {
        const userData = await response.json();
        return { data: { user: userData }, user: userData, error: null };
      } else {
        return { data: { user: null }, user: null, error: new Error('Unable to validate user') };
      }
    } catch (error) {
      console.error('[LocalAuth] Get user failed:', error);
      return { data: { user: null }, user: null, error: error as Error };
    }
  }

  async setSession(session: AuthSession): Promise<{ error: Error | null }> {
    try {
      this.saveSession(session);
      return { error: null };
    } catch (error: any) {
      return { error: error };
    }
  }

  // Mock auth state change handler for compatibility
  onAuthStateChange(callback: (event: string, session: AuthSession | null) => void) {
    // Register subscriber
    this.subscribers.push(callback);

    // Call immediately with current state
    callback('INITIAL_SESSION', this.session);

    // Return unsubscribe function
    return {
      data: {
        subscription: {
          unsubscribe: () => {
            this.subscribers = this.subscribers.filter(cb => cb !== callback);
          }
        }
      }
    };
  }

  // Mock admin interface for compatibility
  get auth() {
    return {
      admin: {
        getUser: this.getUser.bind(this),
        getUserById: (id: string) => this.getUser(),
        listUsers: () => Promise.resolve([]), // Mock implementation
      },
      signInWithPassword: this.signInWithPassword.bind(this),
      signOut: this.signOut.bind(this),
      getSession: this.getSession.bind(this),
      refreshSession: this.refreshSession.bind(this),
      getUser: this.getUser.bind(this),
      setSession: this.setSession.bind(this),
      onAuthStateChange: this.onAuthStateChange.bind(this),
    };
  }
}


export const localAuthClient = new LocalAuthClient();
export default localAuthClient;
