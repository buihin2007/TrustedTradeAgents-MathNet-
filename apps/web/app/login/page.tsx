"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
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
    const { error } = await createClient().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) return setError(error.message);
    router.replace("/requests");
    router.refresh();
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-neutral-200 bg-white p-6">
        <div>
          <h1 className="text-xl font-semibold">Trade Gateway</h1>
          <p className="text-sm text-neutral-500">Buyer workspace. Sign in as the person who signs the mandate.</p>
        </div>
        <input className="w-full rounded border border-neutral-300 px-3 py-2" type="email" placeholder="Email"
          value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        <input className="w-full rounded border border-neutral-300 px-3 py-2" type="password" placeholder="Password"
          value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
        {error && <p className="text-sm text-human">{error}</p>}
        <button disabled={busy} className="w-full rounded bg-auto py-2 font-medium text-white disabled:opacity-60">
          {busy ? "Signing in..." : "Sign in"}
        </button>
        <p className="text-xs text-neutral-500">
          The purchasing agent has no login. It connects to the Valio Aimo gateway through MCP with its own key,
          acting under the mandate you sign.
        </p>
      </form>
    </main>
  );
}
