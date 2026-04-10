import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EmptyState } from '../../src/client/components/EmptyState.js';

describe('EmptyState', () => {
  it('renders no-workflows type correctly', () => {
    render(<EmptyState type="no-workflows" />);
    expect(screen.getByText('No workflows found')).toBeInTheDocument();
    expect(screen.getByText(/Check your workflow directory path/)).toBeInTheDocument();
    expect(screen.getByText('📋')).toBeInTheDocument();
  });

  it('renders no-selection type correctly', () => {
    render(<EmptyState type="no-selection" />);
    expect(screen.getByText('Select a workflow')).toBeInTheDocument();
    expect(screen.getByText(/Click on a workflow from the sidebar/)).toBeInTheDocument();
    expect(screen.getByText('👈')).toBeInTheDocument();
  });
});
