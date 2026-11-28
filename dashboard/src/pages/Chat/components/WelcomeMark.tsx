import styles from "../index.module.less";

export default function WelcomeMark({
  hidden = false,
  animate = false,
}: {
  hidden?: boolean;
  animate?: boolean;
}) {
  return (
    <svg
      className={styles.welcomeLogoIcon}
      viewBox="0 0 256 256"
      fill="currentColor"
      role="img"
      aria-label="Colink"
      aria-hidden={hidden || undefined}
      focusable="false"
      style={{ visibility: hidden ? "hidden" : undefined }}
    >
      <path d="M192.3 58.7 A98 98 0 1 0 192.3 197.3 L166.84 171.84 A62 62 0 1 1 166.84 84.16 Z" />
      <path
        className={animate ? styles.welcomeShortArc : undefined}
        d="M203 79.5 A56 56 0 0 1 203 176.5 L187 148.78 A24 24 0 0 0 187 107.22 Z"
      />
    </svg>
  );
}
