import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/** Refresh the auth cookie on every request and send signed-out users to /login. */
export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (toSet) => {
          toSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          toSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        },
      },
    },
  );
  const { data } = await supabase.auth.getClaims();   // verifies the JWT signature; never trust getSession() here
  const signedIn = Boolean(data?.claims);
  const onLogin = request.nextUrl.pathname.startsWith("/login");
  if (!signedIn && !onLogin) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  if (signedIn && onLogin) {
    const url = request.nextUrl.clone();
    url.pathname = "/requests";
    return NextResponse.redirect(url);
  }
  return response;
}
