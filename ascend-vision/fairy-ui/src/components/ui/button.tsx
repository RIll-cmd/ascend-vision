import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva('inline-flex items-center justify-center gap-2 rounded-xl text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-sky-300 disabled:pointer-events-none disabled:opacity-40', {
  variants: {
    variant: { default: 'bg-sky-100 text-slate-950 hover:bg-white', outline: 'border border-white/10 bg-white/[.025] text-slate-300 hover:bg-white/[.07]', ghost: 'text-slate-400 hover:bg-white/5 hover:text-white' },
    size: { default: 'h-11 px-4', icon: 'h-10 w-10' },
  }, defaultVariants: { variant: 'default', size: 'default' },
});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean }
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...props }, ref) => {
  const Component = asChild ? Slot : 'button';
  return <Component ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});
Button.displayName = 'Button';
