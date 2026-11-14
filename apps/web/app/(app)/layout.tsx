import { redirect } from "next/navigation";
import { Nav } from "@/components/Nav";
import { createClient } from "@/lib/supabase/server";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  if (!data?.claims) redirect("/login");
  return (
    <>
      <Nav email={data.claims.email as string | undefined} />
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </>
  );
}
