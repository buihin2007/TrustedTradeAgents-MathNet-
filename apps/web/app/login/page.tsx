"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Icon, Spinner } from "@/components/Icon";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await createClient().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) return setError(error.message);
    router.replace("/requests");
    router.refresh();
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand text-white">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-lg font-semibold text-brand">Trade Gateway</h1>
            <p className="text-sm text-slate-600">Buyer workspace for Valio Aimo</p>
          </div>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6 shadow-sm">
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium text-slate-700">Email</label>
            <input id="email" className="field" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              required autoComplete="email" />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-sm font-medium text-slate-700">Password</label>
            <input id="password" className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              required autoComplete="current-password" />
          </div>
          {error && (
            <p role="alert" className="flex items-start gap-2 text-sm text-human">
              <Icon name="alert" className="mt-0.5 h-4 w-4" />
              {error}
            </p>
          )}
          <button disabled={busy} className="btn-primary w-full">
            {busy && <Spinner className="h-4 w-4 border-white/40 border-t-white" />}
            {busy ? "Signing in" : "Sign in"}
          </button>
          <p className="text-xs text-slate-500">Sign in as the person who signs the mandate.</p>
        </form>
        <p className="mt-4 flex items-start gap-2 px-1 text-xs text-slate-600">
          <Icon name="bot" className="mt-0.5 h-4 w-4" />
          The purchasing agent has no login. It reaches the Valio Aimo gateway through MCP with its own key and can only act
          within the mandate you sign.
        </p>
      </div>
    </main>
  );
}
