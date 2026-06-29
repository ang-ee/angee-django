import * as React from "react";
import { Slider as BaseSlider } from "@base-ui/react/slider";
import type {
  SliderControlProps as BaseSliderControlProps,
  SliderIndicatorProps as BaseSliderIndicatorProps,
  SliderRootProps as BaseSliderRootProps,
  SliderThumbProps as BaseSliderThumbProps,
  SliderTrackProps as BaseSliderTrackProps,
  SliderValueProps as BaseSliderValueProps,
} from "@base-ui/react/slider";

import { toneSolidBg } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

export const sliderVariants = tv({
  slots: {
    root: "flex w-full min-w-0 items-center gap-3",
    control:
      "relative flex min-w-0 flex-1 touch-none select-none items-center py-2 outline-none focus-within:focus-ring data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60 data-[orientation=vertical]:h-32 data-[orientation=vertical]:w-8 data-[orientation=vertical]:flex-none data-[orientation=vertical]:justify-center data-[orientation=vertical]:px-2 data-[orientation=vertical]:py-0",
    track:
      "relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-inset data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5 data-[orientation=vertical]:flex-none",
    indicator:
      "absolute h-full rounded-full data-[orientation=vertical]:bottom-0 data-[orientation=vertical]:w-full",
    thumb:
      "block size-4 rounded-full border border-border-strong bg-sheet shadow-sm outline-none transition-transform focus-visible:focus-ring data-[disabled]:cursor-not-allowed data-[dragging]:scale-105",
    value: "w-12 shrink-0 text-right text-13 tabular-nums text-fg",
  },
  variants: {
    size: {
      sm: {
        control: "py-1.5",
        track: "h-1",
        thumb: "size-3.5",
        value: "text-xs",
      },
      md: "",
      lg: {
        control: "py-2.5",
        track: "h-2",
        thumb: "size-5",
        value: "text-sm",
      },
    },
    tone: {
      brand: { indicator: toneSolidBg("brand") },
      success: { indicator: toneSolidBg("success") },
      warning: { indicator: toneSolidBg("warning") },
      danger: { indicator: toneSolidBg("danger") },
    },
    orientation: {
      horizontal: "",
      vertical: {
        root: "inline-flex w-auto flex-col",
        value: "w-auto text-center",
      },
    },
  },
  defaultVariants: {
    size: "md",
    tone: "brand",
    orientation: "horizontal",
  },
});

export type SliderRecipeProps = VariantProps<typeof sliderVariants>;
export type SliderSize = NonNullable<SliderRecipeProps["size"]>;
export type SliderTone = NonNullable<SliderRecipeProps["tone"]>;
export type SliderOrientation = NonNullable<SliderRecipeProps["orientation"]>;
export type SliderValueType = number | readonly number[];

export type SliderRootProps<
  Value extends SliderValueType = SliderValueType,
> = Omit<BaseSliderRootProps<Value>, "className" | "orientation"> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderRoot = React.forwardRef<HTMLDivElement, SliderRootProps>(
  function SliderRoot(
    {
      className,
      orientation = "horizontal",
      size = "md",
      tone = "brand",
      ...props
    },
    ref,
  ) {
    const styles = sliderVariants({ orientation, size, tone });
    return (
      <BaseSlider.Root
        ref={ref}
        className={styles.root({ className })}
        orientation={orientation}
        {...props}
      />
    );
  },
);
SliderRoot.displayName = "SliderRoot";

export type SliderControlProps = Omit<BaseSliderControlProps, "className"> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderControl = React.forwardRef<
  HTMLDivElement,
  SliderControlProps
>(function SliderControl(
  { className, orientation = "horizontal", size = "md", tone = "brand", ...props },
  ref,
) {
  const styles = sliderVariants({ orientation, size, tone });
  return (
    <BaseSlider.Control
      ref={ref}
      className={styles.control({ className })}
      {...props}
    />
  );
});
SliderControl.displayName = "SliderControl";

export type SliderTrackProps = Omit<BaseSliderTrackProps, "className"> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderTrack = React.forwardRef<HTMLDivElement, SliderTrackProps>(
  function SliderTrack(
    { className, orientation = "horizontal", size = "md", tone = "brand", ...props },
    ref,
  ) {
    const styles = sliderVariants({ orientation, size, tone });
    return (
      <BaseSlider.Track
        ref={ref}
        className={styles.track({ className })}
        {...props}
      />
    );
  },
);
SliderTrack.displayName = "SliderTrack";

export type SliderIndicatorProps = Omit<
  BaseSliderIndicatorProps,
  "className"
> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderIndicator = React.forwardRef<
  HTMLDivElement,
  SliderIndicatorProps
>(function SliderIndicator(
  { className, orientation = "horizontal", size = "md", tone = "brand", ...props },
  ref,
) {
  const styles = sliderVariants({ orientation, size, tone });
  return (
    <BaseSlider.Indicator
      ref={ref}
      className={styles.indicator({ className })}
      {...props}
    />
  );
});
SliderIndicator.displayName = "SliderIndicator";

export type SliderThumbProps = Omit<BaseSliderThumbProps, "className"> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderThumb = React.forwardRef<HTMLDivElement, SliderThumbProps>(
  function SliderThumb(
    {
      className,
      getAriaLabel = () => "Slider value",
      orientation = "horizontal",
      size = "md",
      tone = "brand",
      ...props
    },
    ref,
  ) {
    const styles = sliderVariants({ orientation, size, tone });
    return (
      <BaseSlider.Thumb
        ref={ref}
        className={styles.thumb({ className })}
        getAriaLabel={getAriaLabel}
        {...props}
      />
    );
  },
);
SliderThumb.displayName = "SliderThumb";

export type SliderValueProps = Omit<BaseSliderValueProps, "className"> &
  SliderRecipeProps & {
    className?: string;
  };

export const SliderValue = React.forwardRef<HTMLOutputElement, SliderValueProps>(
  function SliderValue(
    { className, orientation = "horizontal", size = "md", tone = "brand", ...props },
    ref,
  ) {
    const styles = sliderVariants({ orientation, size, tone });
    return (
      <BaseSlider.Value
        ref={ref}
        className={styles.value({ className })}
        {...props}
      />
    );
  },
);
SliderValue.displayName = "SliderValue";

export type SliderProps = Omit<SliderRootProps, "children"> & {
  controlClassName?: string;
  formatValue?: (value: number, formattedValue: string) => React.ReactNode;
  formatValues?: (
    values: readonly number[],
    formattedValues: readonly string[],
  ) => React.ReactNode;
  indicatorClassName?: string;
  showValue?: boolean;
  thumbClassName?: string;
  thumbCount?: number;
  thumbLabel?: string;
  thumbLabels?: readonly string[];
  trackClassName?: string;
  valueClassName?: string;
};

export const Slider = React.forwardRef<HTMLDivElement, SliderProps>(
  function Slider(
    {
      controlClassName,
      defaultValue,
      formatValue,
      formatValues,
      indicatorClassName,
      orientation = "horizontal",
      showValue = false,
      size = "md",
      thumbClassName,
      thumbCount,
      thumbLabel,
      thumbLabels,
      tone = "brand",
      trackClassName,
      value,
      valueClassName,
      ...props
    },
    ref,
  ) {
    const valueThumbs = Array.isArray(value) ? value.length : undefined;
    const defaultThumbs = Array.isArray(defaultValue)
      ? defaultValue.length
      : undefined;
    const resolvedThumbCount = Math.max(
      1,
      thumbCount ?? valueThumbs ?? defaultThumbs ?? 1,
    );

    function getThumbLabel(index: number): string {
      return thumbLabels?.[index] ?? thumbLabel ?? "Slider value";
    }

    return (
      <SliderRoot
        ref={ref}
        defaultValue={defaultValue}
        orientation={orientation}
        size={size}
        tone={tone}
        value={value}
        {...props}
      >
        <SliderControl
          orientation={orientation}
          size={size}
          tone={tone}
          className={controlClassName}
        >
          <SliderTrack
            orientation={orientation}
            size={size}
            tone={tone}
            className={trackClassName}
          >
            <SliderIndicator
              orientation={orientation}
              size={size}
              tone={tone}
              className={indicatorClassName}
            />
          </SliderTrack>
          {Array.from({ length: resolvedThumbCount }, (_, index) => (
            <SliderThumb
              key={index}
              index={index}
              orientation={orientation}
              size={size}
              tone={tone}
              className={thumbClassName}
              getAriaLabel={getThumbLabel}
            />
          ))}
        </SliderControl>
        {showValue ? (
          <SliderValue
            orientation={orientation}
            size={size}
            tone={tone}
            className={valueClassName}
          >
            {(formattedValues, values) => {
              if (formatValues) return formatValues(values, formattedValues);
              if (values.length > 1) return formattedValues.join(" - ");

              const firstValue = values[0] ?? 0;
              const firstFormatted = formattedValues[0] ?? String(firstValue);
              return formatValue
                ? formatValue(firstValue, firstFormatted)
                : firstFormatted;
            }}
          </SliderValue>
        ) : null}
      </SliderRoot>
    );
  },
);
Slider.displayName = "Slider";
