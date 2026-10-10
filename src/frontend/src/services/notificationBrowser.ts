import { browserPushRegistration } from "./pwa";
import {
  createBrowserDestination,
  removeBrowserDestination,
  type BrowserPushConfiguration,
} from "./notifications";

export function browserPushSupported() {
  return (
    window.isSecureContext &&
    "Notification" in window &&
    "PushManager" in window &&
    "serviceWorker" in navigator
  );
}

export async function enableBrowserPush(
  configuration: BrowserPushConfiguration,
  label: string,
) {
  if (
    !browserPushSupported() ||
    !configuration.enabled ||
    !configuration.session_authenticated
  )
    throw new Error(
      "Sign in on a supported HTTPS browser with PWA delivery enabled.",
    );
  // Request directly from the click, before any network operation consumes the gesture.
  if ((await Notification.requestPermission()) !== "granted")
    throw new Error(
      "Browser permission was not granted. Update this site's browser permissions to try again.",
    );
  const registration = await browserPushRegistration();
  if (configuration.current_destination_id)
    await removeBrowserDestination(configuration.current_destination_id);
  const previous = await registration.pushManager.getSubscription();
  if (previous) await previous.unsubscribe();
  const key = Uint8Array.from(
    atob(configuration.public_key.replace(/-/g, "+").replace(/_/g, "/")),
    (character) => character.charCodeAt(0),
  );
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: key,
  });
  try {
    return await createBrowserDestination(
      subscription.toJSON(),
      configuration.public_key,
      label,
    );
  } catch (reason) {
    await subscription.unsubscribe().catch(() => {});
    throw reason;
  }
}
