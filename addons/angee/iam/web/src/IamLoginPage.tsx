import { useEffect, useState, type ReactNode } from "react";
import { LoginPage, type LoginPageProps } from "@angee/base";

const LOGIN_BACKGROUND_ROTATION_MS = 15_000;
const IAM_STATIC_URL = "/static/angee/iam";

export const IAM_LOGIN_BACKGROUND_IMAGE_URLS = [
  `${IAM_STATIC_URL}/backgrounds/angee-children-build.webp`,
  `${IAM_STATIC_URL}/backgrounds/angee-children-future.webp`,
  `${IAM_STATIC_URL}/backgrounds/angee-future-city.webp`,
  `${IAM_STATIC_URL}/backgrounds/angee-pond-walk.webp`,
  `${IAM_STATIC_URL}/backgrounds/angee-vision-cinimatic.webp`,
  `${IAM_STATIC_URL}/backgrounds/angee-vision-daytime.webp`,
] as const;

export interface IamLoginPageProps
  extends Omit<LoginPageProps, "backgroundImageUrl"> {
  backgroundImageUrls?: readonly string[];
}

export function IamLoginPage({
  backgroundImageUrls = IAM_LOGIN_BACKGROUND_IMAGE_URLS,
  ...props
}: IamLoginPageProps): ReactNode {
  const [backgroundImageUrl, setBackgroundImageUrl] = useState(() =>
    pickRandomLoginBackgroundUrl(backgroundImageUrls),
  );

  useEffect(() => {
    if (backgroundImageUrls.length < 2) return undefined;

    const interval = window.setInterval(() => {
      setBackgroundImageUrl((current) =>
        pickRandomLoginBackgroundUrl(backgroundImageUrls, current),
      );
    }, LOGIN_BACKGROUND_ROTATION_MS);

    return () => window.clearInterval(interval);
  }, [backgroundImageUrls]);

  return <LoginPage {...props} backgroundImageUrl={backgroundImageUrl} />;
}

function pickRandomLoginBackgroundUrl(
  urls: readonly string[],
  previousUrl?: string,
): string | undefined {
  if (urls.length === 0) return undefined;
  if (urls.length === 1) return urls[0];

  const candidates = previousUrl
    ? urls.filter((url) => url !== previousUrl)
    : urls;
  return candidates[Math.floor(Math.random() * candidates.length)] ?? urls[0];
}
