import type { NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  // skip static files and /api (the buyer API checks the JWT itself)
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico).*)"],
};
